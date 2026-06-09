from machine import I2C, Pin
import time

# 初期化
bus = I2C(
    0,
    scl=Pin(6),
    sda=Pin(7),
    freq=100_000
)

RPR0521RS_I2C_ADDR = 0x38

def start_rpr():
    try:
        bus.writeto(RPR0521RS_I2C_ADDR, b'\x41\xc6')
        bus.writeto(RPR0521RS_I2C_ADDR, b'\x42\x03')
        bus.writeto(RPR0521RS_I2C_ADDR, b'\x43\x20')
        time.sleep_ms(100)
    except Exception as error:
        print("RPR-0521RS STOP", error)

def get_rpr():
    """
    RPR-0521RS から (lux, proximity_raw) を取得
    CRC 無しセンサなのでエラー時には None, None
    """
    try:
        raw = bus.readfrom_mem(RPR0521RS_I2C_ADDR, 0x44, 6)
        ps    = (raw[1] << 8) | raw[0]
        als0  = (raw[3] << 8) | raw[2]
        als1  = (raw[5] << 8) | raw[4]

        ratio = als1 / als0

        if   ratio < 0.595: lux = 1.682 * als0 - 1.877 * als1
        elif ratio < 1.015: lux = 0.644 * als0 - 0.132 * als1
        elif ratio < 1.352: lux = 0.756 * als0 - 0.243 * als1
        elif ratio < 3.053: lux = 0.766 * als0 - 0.250 * als1
        else:               lux = 0

        return lux, ps

    except Exception as error:
        print("RPR-0521RS Error", error)
        return None, None


start_rpr()

while True:
    time.sleep(1)

    rpr_lux, rpr_ps = get_rpr()


    print("RPR-0521RS Lux / PS : {:.2f} lx, ps={}".format(rpr_lux, rpr_ps) if rpr_lux else "RPR-0521RS : ---")
