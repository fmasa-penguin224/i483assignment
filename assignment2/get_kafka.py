from kafka import KafkaConsumer, KafkaProducer
from collections import deque

KAFKA_SERVER = "150.65.230.59:9092"

KAFKA_SUB_TOPICS = [
    "i483-sensors-s2510155-BH1750-illumination",
    "i483-sensors-s2510155-SCD41-co2"
]

BH1750_TOPIC = "i483-sensors-s2510155-BH1750-illumination"
BH1750_AVG_TOPIC = "i483-sensors-s2510155-BH1750_avg-illumination"

CO2_TOPIC = "i483-sensors-s2510155-SCD41-co2"
CO2_THRESHOLD_TOPIC = "i483-actuators-s2510155-co2_threshold-crossed"

CO2_THRESHOLD = 700

consumer = KafkaConsumer(
    bootstrap_servers=[KAFKA_SERVER],
    auto_offset_reset="latest",
    enable_auto_commit=True,
    group_id="s2510155",
    value_deserializer=lambda x: x.decode("utf-8")
)

producer = KafkaProducer(
    bootstrap_servers=[KAFKA_SERVER],
    value_serializer=lambda x: str(x).encode("utf-8")
)

consumer.subscribe(KAFKA_SUB_TOPICS)

print("Kafkaトピックを購読中:")
for topic in KAFKA_SUB_TOPICS:
    print("-", topic)

print("Kafka配信先:")
print("-", BH1750_AVG_TOPIC)
print("-", CO2_THRESHOLD_TOPIC)

# 15秒に1回 × 20件 = 直近5分間相当
ill_queue = deque([], 20)

ill_receive_count = 0

for message in consumer:
    print("----- 受信 -----")
    print("Topic :", message.topic)
    print("Value :", message.value)

    # =========================
    # BH1750 照度平均
    # =========================
    if message.topic == BH1750_TOPIC:
        try:
            ill_value = float(message.value)
        except ValueError:
            print("BH1750の値を数値に変換できません:", message.value)
            continue

        ill_queue.append(ill_value)
        ill_receive_count += 1

        print("BH1750 illumination:", ill_value)
        print("Queue size:", len(ill_queue))

        # 2回受信したら平均値を計算して配信
        if ill_receive_count >= 2:
            ill_receive_count = 0

            avg_ill = sum(ill_queue) / len(ill_queue)
            avg_ill = "{:.2f}".format(avg_ill)
            avg_ill_str = str(avg_ill)
            producer.send(BH1750_AVG_TOPIC, avg_ill_str)
            producer.flush()

            print("----- BH1750 平均値を配信 -----")
            print("Publish Topic:", BH1750_AVG_TOPIC)
            print("Average:", avg_ill_str)
            print("Queue size:", len(ill_queue))

    # =========================
    # SCD41 CO2 しきい値判定
    # =========================
    elif message.topic == CO2_TOPIC:
        try:
            co2_value = float(message.value)
        except ValueError:
            print("CO2の値を数値に変換できません:", message.value)
            continue

        if co2_value > CO2_THRESHOLD:
            result = "yes"
        else:
            result = "no"

        producer.send(CO2_THRESHOLD_TOPIC, result)
        producer.flush()

        print("----- CO2 判定結果を配信 -----")
        print("CO2:", co2_value)
        print("Threshold:", CO2_THRESHOLD)
        print("Result:", result)
        print("Publish Topic:", CO2_THRESHOLD_TOPIC)
