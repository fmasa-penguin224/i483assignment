from machine import I2C, Pin
import time


bus = I2C(
    0,
    scl=Pin(6),
    sda=Pin(7),
    freq=100_000
)


SCD41_I2C_ADDR    = 0x62

#  CRC-8 (poly 0x31) 

def check_crc(data):
    """3 byte ブロックの CRC-8 を計算し一致判定に用いる"""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x31
            else:
                crc = crc << 1
            crc &= 0xFF
    return crc

def start_scd():
    """測定開始コマンド"""
    bus.writeto(SCD41_I2C_ADDR, b'\x21\xb1')
    time.sleep_ms(100)

def stop_scd():
    """測定停止コマンド"""
    bus.writeto(SCD41_I2C_ADDR, b'\x3f\x86')
    time.sleep_ms(100)

def get_scd():
    """
    SCD41 から (CO₂[ppm], T[°C], H[%]) を取得
    CRC エラー時はすべて None を返す
    """
    bus.writeto(SCD41_I2C_ADDR, b'\xec\x05')
    time.sleep_ms(100)

    data = bus.readfrom(SCD41_I2C_ADDR, 9)

    def decode(off):
        raw = data[off:off+2]
        crc = data[off+2]
        if check_crc(raw) != crc:
            return None
        return (raw[0] << 8) | raw[1]

    raw_co2  = decode(0)
    raw_temp = decode(3)
    raw_humi = decode(6)

    if None in (raw_co2, raw_temp, raw_humi):
        return None, None, None

    co2  = raw_co2
    temp = -45 + 175 * (raw_temp / 65535)
    humi = 100 * (raw_humi / 65535)

    return co2, temp, humi

#  main
start_scd()
print("Getting SCD41 Ready...")

while True:
    time.sleep(5)

    scd_co2, scd_t, scd_h = get_scd()

    print("SCD41 CO₂/T/H : {:.0f} ppm, {:.2f} °C, {:.2f} %".format(scd_co2, scd_t, scd_h) if scd_co2 else "SCD41 : ---")