from machine import I2C, Pin
import time

#初期化
bus = I2C(
    0,
    scl=Pin(6),
    sda=Pin(7),
    freq=100_000
)

BH1750_I2C_ADDR   = 0x23
BH1750_CMD        = 0x10

def get_bh1750():
    # BH1750 から照度 [lx] を取得（失敗時 None を返す）
    try:
        bus.writeto(BH1750_I2C_ADDR, bytearray([BH1750_CMD]))
        time.sleep_ms(100)

        resp  = bus.readfrom(BH1750_I2C_ADDR, 2)
        value = (resp[0] << 8) | resp[1]

        return value / 1.2

    except Exception as error:
        print("BH1750 Error:", error)
        return None


# main

while True:
    time.sleep(1)

    bh_lux = get_bh1750()

    print("BH1750 Lux : {:.2f} lx".format(bh_lux) if bh_lux else "BH1750 Lux : ---")