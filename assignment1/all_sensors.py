from machine import I2C, Pin
import time

bus = I2C(
    0,                 # I²C bus ID ―― ESP32 の I2C(0) を使用
    scl=Pin(6),       # SCL → GPIO32
    sda=Pin(7),       # SDA → GPIO33
    freq=100_000       # 標準モード 100 kHz
)

BH1750_I2C_ADDR   = 0x23          # 照度センサ BH1750
BH1750_CMD        = 0x10          # 連続高分解能モード（1 lx／120 ms）

SCD41_I2C_ADDR    = 0x62          # CO₂/温度/湿度 SCD41

RPR0521RS_I2C_ADDR = 0x38         # ALS＋PS RPR-0521RS

DPS310_I2C_ADDR   = 0x77          # 気圧／温度 DPS310

def get_bh1750():
    """BH1750 から照度 [lx] を取得（失敗時 None を返す）"""
    try:
        # 測定モードを送信
        bus.writeto(BH1750_I2C_ADDR, bytearray([BH1750_CMD]))
        time.sleep_ms(100)

        # 16-bit データ読み出し（MSB → LSB）
        resp  = bus.readfrom(BH1750_I2C_ADDR, 2)
        value = (resp[0] << 8) | resp[1]       # 16 bit → 10-lux raw

        # データシートの係数 1.2 で lux に変換
        return value / 1.2

    except Exception as error:
        print("BH1750 Error:", error)
        return None                            # 測定失敗時

def check_crc(data):
    """3 byte ブロックの CRC-8 を計算し一致判定に用いる"""
    crc = 0xFF                                 # 初期値
    for byte in data:
        crc ^= byte
        for _ in range(8):                     # 8 シフト分
            if crc & 0x80:                     # MSB = 1 ?
                crc = (crc << 1) ^ 0x31
            else:
                crc = crc << 1
            crc &= 0xFF                        # 8 bit マスク
    return crc

def start_scd():
    #測定開始
    bus.writeto(SCD41_I2C_ADDR, b'\x21\xb1')
    time.sleep_ms(100)

def stop_scd():
    #測定停止
    bus.writeto(SCD41_I2C_ADDR, b'\x3f\x86')
    time.sleep_ms(100)

def get_scd():
    """
    SCD41 から (CO₂[ppm], T[°C], H[%]) を取得  
    CRC エラー時はすべて None を返す
    """
    # データ読み出し要求
    bus.writeto(SCD41_I2C_ADDR, b'\xec\x05')   # read_measurement
    time.sleep_ms(100)                         # センサ側内部遅延

    data = bus.readfrom(SCD41_I2C_ADDR, 9)     # 9-byte フレーム

    # --- オフセット位置より 16-bit + CRC を復号 -------- #
    def decode(off):
        raw = data[off:off+2]                  # 2-byte データ部
        crc = data[off+2]                      # 後続 CRC-8
        if check_crc(raw) != crc:              # CRC 不一致 → エラー
            return None
        return (raw[0] << 8) | raw[1]          # 正常 → 16-bit int

    # 各データを抽出
    raw_co2  = decode(0)
    raw_temp = decode(3)
    raw_humi = decode(6)

    if None in (raw_co2, raw_temp, raw_humi):  # いずれか CRC エラー
        return None, None, None

    # データシート準拠の線形式で実値化
    co2  = raw_co2
    temp = -45 + 175 * (raw_temp / 65535)
    humi = 100 * (raw_humi / 65535)

    return co2, temp, humi

def start_rpr():
    """ALS/PS 設定レジスタを初期化して測定開始"""
    #RPR0521RS_I2C_ADDR = 0x38
    try:
        # レジスタ 0x41-0x43 に書き込み
        bus.writeto(RPR0521RS_I2C_ADDR, b'\x41\xc6')  # MODE CONTROL
        bus.writeto(RPR0521RS_I2C_ADDR, b'\x42\x03')  # ALS PS CONTROL
        bus.writeto(RPR0521RS_I2C_ADDR, b'\x43\x20')  # PS CONTROL
        time.sleep_ms(100)
    except Exception as error:
        print("RPR-0521RS STOP", error)

def get_rpr():
    """
    RPR-0521RS から (lux, proximity_raw) を取得  
    CRC 無しセンサなのでエラー時には None, None
    """
    try:
        # 測定値 6-byte をまとめて読む（ALS0/1 + PS）
        raw = bus.readfrom_mem(RPR0521RS_I2C_ADDR, 0x44, 6)
        ps    = (raw[1] << 8) | raw[0]
        als0  = (raw[3] << 8) | raw[2]                 # Visible
        als1  = (raw[5] << 8) | raw[4]                 # IR

        ratio = als1 / als0                            # for lux formula

        # データシート推奨の区分線形補正
        if   ratio < 0.595: lux = 1.682 * als0 - 1.877 * als1
        elif ratio < 1.015: lux = 0.644 * als0 - 0.132 * als1
        elif ratio < 1.352: lux = 0.756 * als0 - 0.243 * als1
        elif ratio < 3.053: lux = 0.766 * als0 - 0.250 * als1
        else:               lux = 0

        return lux, ps

    except Exception as error:
        print("RPR-0521RS Error", error)
        return None, None


def twos_complement(raw, bit):
    """ビット幅 bit の 2 の補数 → 符号付き整数へ"""
    msb = 1 << (bit - 1)
    if raw & msb:
        raw -= 1 << bit
    return raw

def get_calibration_coefficients():
    """
    DPS310 内蔵 NVM からキャリブレーション係数 9 個を取得し
    dict で返す
    """
    coefficients = bus.readfrom_mem(DPS310_I2C_ADDR, 0x10, 18)

    c_raw = {}

    c_raw['c0']  = twos_complement((coefficients[0] << 4) |
                                   ((coefficients[1] >> 4) & 0x0F), 12)
    c_raw['c1']  = twos_complement(((coefficients[1] & 0x0F) << 8) |
                                    coefficients[2], 12)
    c_raw['c00'] = twos_complement((coefficients[3] << 12) |
                                   (coefficients[4] << 4) |
                                   ((coefficients[5] >> 4) & 0x0F), 20)
    c_raw['c10'] = twos_complement(((coefficients[5] & 0x0F) << 16) |
                                   (coefficients[6] << 8) |
                                    coefficients[7], 20)
    c_raw['c01'] = twos_complement((coefficients[8]  << 8) | coefficients[9], 16)
    c_raw['c11'] = twos_complement((coefficients[10] << 8) | coefficients[11], 16)
    c_raw['c20'] = twos_complement((coefficients[12] << 8) | coefficients[13], 16)
    c_raw['c21'] = twos_complement((coefficients[14] << 8) | coefficients[15], 16)
    c_raw['c30'] = twos_complement((coefficients[16] << 8) | coefficients[17], 16)

    return c_raw

def start_dps():
    """DPS310 の測定モード & レートを設定"""
    try:
        # レジスタ 0x06-0x09 に書き込み
        bus.writeto(DPS310_I2C_ADDR, b'\x06\x71')
        bus.writeto(DPS310_I2C_ADDR, b'\x07\xF0')
        bus.writeto(DPS310_I2C_ADDR, b'\x08\x07')
        bus.writeto(DPS310_I2C_ADDR, b'\x09\x00')
        time.sleep_ms(100)
    except Exception as error:
        print("DPS310 STOP", error)

def get_dps(c_raw):
    """
    DPS310 から (T[°C], P[hPa]) を取得  
    """
    try:
        # 生データ 3-byte ×2（T / P）を読み出し
        temp_bytes = bus.readfrom_mem(DPS310_I2C_ADDR, 0x03, 3)
        pres_bytes = bus.readfrom_mem(DPS310_I2C_ADDR, 0x00, 3)

        temp_raw = twos_complement(
            (temp_bytes[0] << 16) | (temp_bytes[1] << 8) | temp_bytes[2], 24)
        pres_raw = twos_complement(
            (pres_bytes[0] << 16) | (pres_bytes[1] << 8) | pres_bytes[2], 24)

        scale_temp = temp_raw / 524288.0
        scale_pres = pres_raw / 1572864.0

        # 気温計算
        temp = c_raw['c0'] * 0.5 + c_raw['c1'] * scale_temp
        # 気圧計算
        pres = (
            c_raw['c00'] + scale_pres *
            (c_raw['c10'] + scale_pres * (c_raw['c20'] +
             scale_pres * c_raw['c30'])) +
            scale_temp *
            (c_raw['c01'] + scale_pres * (c_raw['c11'] +
             scale_pres * c_raw['c21']))
        )

        return temp, pres / 100                # Pa → hPa

    except Exception as error:
        print("DPS310 Error", error)
        return None, None


# センサ初期化
start_dps()
start_rpr()
start_scd()
print("Getting SCD41 Ready...")

# ループ：15 s 間 5 s 間隔で測定
start = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), start) < 15_000:
    time.sleep(5)                               # 5 s インターバル

    # 各センサの測定値を取得
    bh_lux           = get_bh1750()
    rpr_lux, rpr_ps  = get_rpr()
    dps_temp, dps_pr = get_dps(get_calibration_coefficients())
    scd_co2, scd_t, scd_h = get_scd()


    print("-------------------------- Results --------------------------")
    print("BH1750 Lux : {:.2f} lx".format(bh_lux)        if bh_lux else "BH1750 Lux : ---")
    print("RPR-0521RS Lux / PS : {:.2f} lx, ps={}".format(rpr_lux, rpr_ps) if rpr_lux else "RPR-0521RS : ---")
    print("DPS310 T/P : {:.2f} °C, {:.2f} hPa".format(dps_temp, dps_pr) if dps_temp else "DPS310 : ---")
    print("SCD41 CO₂/T/H : {:.0f} ppm, {:.2f} °C, {:.2f} %".format(scd_co2, scd_t, scd_h) if scd_co2 else "SCD41 : ---")
    print("-------------------------------------------------------------")

stop_scd()
