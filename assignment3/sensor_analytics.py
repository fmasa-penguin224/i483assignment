from collections import defaultdict, deque
from typing import Iterable

from pyflink.common import Configuration, Time, WatermarkStrategy, Duration
from pyflink.common.serialization import SimpleStringSchema
from pyflink.common.typeinfo import Types
from pyflink.common.watermark_strategy import TimestampAssigner
from pyflink.datastream import StreamExecutionEnvironment, ProcessWindowFunction
from pyflink.datastream.functions import FlatMapFunction, ProcessFunction
from pyflink.datastream.connectors.kafka import FlinkKafkaConsumer, FlinkKafkaProducer
from pyflink.datastream.window import SlidingEventTimeWindows, TimeWindow


# ============================================================
# 基本設定
# ============================================================

BOOTSTRAP_SERVERS = "150.65.230.59:9092"

INPUT_TOPIC = "i483-allsensors"
OUTPUT_TOPIC = "i483-fvtt"

# 自分の学生ID
ANALYTICS_STUDENT = "s2510155"

# 自分のセンサーだけ処理する
SOURCE_STUDENT = "s2510155"


# ============================================================
# 在不在判定用設定
# ============================================================

# CO2がこの値以上なら在室と判定
CO2_OCCUPIED_THRESHOLD = 800.0

# 照度変化率の閾値
# 単位: lux/sec
LIGHT_RATE_THRESHOLD = 20.0

# 照度履歴を保持する時間
LIGHT_WINDOW_SECONDS = 300


# ============================================================
# topic名の分解
# ============================================================

def parse_sensor_topic(topic: str):
    """
    例:
      i483-sensors-s2510155-SCD41-co2
      i483-sensors-s2510155-BH1750-illumination
    """
    parts = topic.split("-")

    if len(parts) < 5:
        raise ValueError(f"invalid topic: {topic}")

    source_student = parts[2]
    sensor = parts[3].upper()
    data_type = "-".join(parts[4:]).replace("-", "_").lower()

    return source_student, sensor, data_type


# ============================================================
# 入力メッセージの検証
# ============================================================

class ParseAllSensorsMessage(FlatMapFunction):
    """
    i483-allsensors から来る String を検証して、
    自分のセンサーだけ tuple[str, int, float] に変換する。

    入力:
      topic,timestamp,value
    """

    def flat_map(self, message: str):
        try:
            message = message.strip()
            parts = message.split(",")

            if len(parts) != 3:
                print(f"skip invalid format: {message}")
                return

            topic = parts[0].strip()
            timestamp_text = parts[1].strip()
            value_text = parts[2].strip()

            if "-analytics-" in topic:
                return

            if not topic.startswith("i483-sensors-"):
                print(f"skip invalid topic: {topic}")
                return

            source_student, sensor, data_type = parse_sensor_topic(topic)

            # 自分のセンサー以外は無視
            if source_student != SOURCE_STUDENT:
                return

            timestamp = int(timestamp_text)
            value = float(value_text)

            yield topic, timestamp, value

        except Exception as e:
            print(f"skip invalid message: {message}, error={e}")
            return


# ============================================================
# Event Time用 timestamp assigner
# ============================================================

class SensorTimestampAssigner(TimestampAssigner):
    def extract_timestamp(self, value, record_timestamp):
        return value[1]


# ============================================================
# topic名の生成
# ============================================================

def make_analytics_topic(source_topic: str, stat: str):
    """
    min/max/avg用の analytics topic 名を作る。
    """
    source_student, sensor, data_type = parse_sensor_topic(source_topic)

    return (
        f"i483-sensors-{ANALYTICS_STUDENT}-analytics-"
        f"{source_student}_{sensor}_{stat}-{data_type}"
    )


def make_presence_topic(source_student: str):
    """
    在不在判定用の topic 名を作る。

    出力はこれ1つだけ:
      i483-sensors-s2510155-analytics-s2510155_PRESENCE_state-presence
    """
    return (
        f"i483-sensors-{ANALYTICS_STUDENT}-analytics-"
        f"{source_student}_PRESENCE_state-presence"
    )


# ============================================================
# Window集計 min / max / avg
# ============================================================

class StatsWindowFunction(ProcessWindowFunction):
    """
    元topicごとに、直近5分間の値から
    min / max / avg を計算する。
    """

    def process(
        self,
        key: str,
        context: ProcessWindowFunction.Context[TimeWindow],
        elements: Iterable[tuple],
    ):
        values = []

        for record in elements:
            source_topic, timestamp, value = record
            values.append(value)

        if len(values) == 0:
            return

        min_value = min(values)
        max_value = max(values)
        avg_value = sum(values) / len(values)

        for stat, value in [
            ("min", min_value),
            ("max", max_value),
            ("avg", avg_value),
        ]:
            analytics_topic = make_analytics_topic(key, stat)
            yield f"{analytics_topic},{value:.2f}"


# ============================================================
# CO2 + 照度変化率による在不在判定
# ============================================================

class PresenceDetectionFunction(ProcessFunction):
    """
    SCD41のCO2値とBH1750の照度変化率で在不在を判定する。

    出力は1つだけ:
      i483-sensors-s2510155-analytics-s2510155_PRESENCE_state-presence,1
      i483-sensors-s2510155-analytics-s2510155_PRESENCE_state-presence,0
    """

    def __init__(self):
        # 学生ごとに照度履歴を保持
        self.light_map = defaultdict(deque)

        # 学生ごとの最新CO2値
        self.latest_co2_map = {}

    def process_element(self, record, ctx: ProcessFunction.Context):
        source_topic, timestamp, value = record

        try:
            source_student, sensor, data_type = parse_sensor_topic(source_topic)
        except Exception:
            return

        current_time_sec = timestamp / 1000.0

        # --------------------------------------------------------
        # CO2値の更新
        # --------------------------------------------------------
        if sensor == "SCD41" and data_type == "co2":
            self.latest_co2_map[source_student] = value

        # --------------------------------------------------------
        # BH1750照度履歴の更新
        # --------------------------------------------------------
        if sensor == "BH1750" and data_type == "illumination":
            q = self.light_map[source_student]
            q.append((current_time_sec, value))

            border_time = current_time_sec - LIGHT_WINDOW_SECONDS
            while q and q[0][0] < border_time:
                q.popleft()

        # --------------------------------------------------------
        # CO2判定
        # --------------------------------------------------------
        latest_co2 = self.latest_co2_map.get(source_student)

        co2_occupied = False
        if latest_co2 is not None:
            co2_occupied = latest_co2 >= CO2_OCCUPIED_THRESHOLD

        # --------------------------------------------------------
        # 照度変化率判定
        # --------------------------------------------------------
        light_rate = 0.0
        light_changed = False

        light_queue = self.light_map[source_student]

        if len(light_queue) >= 2:
            old_time, old_light = light_queue[-2]
            new_time, new_light = light_queue[-1]

            dt = new_time - old_time

            if dt > 0:
                light_rate = abs(new_light - old_light) / dt

                if light_rate >= LIGHT_RATE_THRESHOLD:
                    light_changed = True

        # --------------------------------------------------------
        # 在不在判定
        # --------------------------------------------------------
        presence = co2_occupied or light_changed
        presence_value = "1" if presence else "0"

        presence_topic = make_presence_topic(source_student)

        # 出力はこれ1つだけ
        yield f"{presence_topic},{presence_value}"


# ============================================================
# メイン処理
# ============================================================

def main():
    config = Configuration().set_string("python.execution-mode", "thread")

    env = StreamExecutionEnvironment.get_execution_environment(config)
    env.set_parallelism(1)

    consumer = FlinkKafkaConsumer(
        topics=INPUT_TOPIC,
        deserialization_schema=SimpleStringSchema(),
        properties={
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "group.id": f"pyflink-{ANALYTICS_STUDENT}-analytics",
            "auto.offset.reset": "latest",
        },
    )

    raw_stream = env.add_source(consumer)

    parsed_stream = raw_stream.flat_map(
        ParseAllSensorsMessage(),
        output_type=Types.TUPLE([
            Types.STRING(),  # source_topic
            Types.LONG(),    # timestamp
            Types.DOUBLE(),  # value
        ]),
    )

    timed_stream = parsed_stream.assign_timestamps_and_watermarks(
        WatermarkStrategy
        .for_bounded_out_of_orderness(Duration.of_seconds(5))
        .with_timestamp_assigner(SensorTimestampAssigner())
    )

    # min / max / avg
    stats_stream = (
        timed_stream
        .key_by(lambda record: record[0])
        .window(
            SlidingEventTimeWindows.of(
                Time.minutes(5),
                Time.seconds(30),
            )
        )
        .process(
            StatsWindowFunction(),
            output_type=Types.STRING(),
        )
    )

    # 在不在判定
    presence_stream = timed_stream.process(
        PresenceDetectionFunction(),
        output_type=Types.STRING(),
    )

    output_stream = stats_stream.union(presence_stream)

    output_stream.print()

    producer = FlinkKafkaProducer(
        topic=OUTPUT_TOPIC,
        serialization_schema=SimpleStringSchema(),
        producer_config={
            "bootstrap.servers": BOOTSTRAP_SERVERS,
        },
    )

    output_stream.add_sink(producer)

    env.execute("sensor_analytics_with_single_presence_output")


if __name__ == "__main__":
    main()