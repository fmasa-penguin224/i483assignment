#include <stdio.h>
#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "driver/i2c.h"
#include "driver/gpio.h"

#include "esp_err.h"

#define I2C_PORT I2C_NUM_0

// 初期化
#define I2C_SDA 7
#define I2C_SCL 6
#define I2C_FREQ 100000

#define RPR_ADDR 0x38

#define RPR_REG_MODE    0x41
#define RPR_REG_ALS_PS  0x42
#define RPR_REG_PS_CTRL 0x43
#define RPR_REG_DATA    0x44

static esp_err_t i2c_init(void)
{
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = I2C_SDA,
        .scl_io_num = I2C_SCL,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = I2C_FREQ,
    };

    esp_err_t ret = i2c_param_config(I2C_PORT, &conf);
    if (ret != ESP_OK) {
        return ret;
    }

    return i2c_driver_install(I2C_PORT, I2C_MODE_MASTER, 0, 0, 0);
}

static esp_err_t rpr_write_reg(uint8_t reg, uint8_t value)
{
    uint8_t data[2] = {reg, value};

    return i2c_master_write_to_device(
        I2C_PORT,
        RPR_ADDR,
        data,
        2,
        pdMS_TO_TICKS(1000)
    );
}

static esp_err_t rpr_init(void)
{
    esp_err_t ret;

    ret = rpr_write_reg(RPR_REG_MODE, 0xC6);
    if (ret != ESP_OK) {
        return ret;
    }

    ret = rpr_write_reg(RPR_REG_ALS_PS, 0x03);
    if (ret != ESP_OK) {
        return ret;
    }

    ret = rpr_write_reg(RPR_REG_PS_CTRL, 0x20);
    if (ret != ESP_OK) {
        return ret;
    }

    return ESP_OK;
}

static esp_err_t rpr_read(uint16_t *ps, uint16_t *als0, uint16_t *als1)
{
    uint8_t reg = RPR_REG_DATA;
    uint8_t raw[6];

    esp_err_t ret = i2c_master_write_read_device(
        I2C_PORT,
        RPR_ADDR,
        &reg,
        1,
        raw,
        6,
        pdMS_TO_TICKS(1000)
    );

    if (ret != ESP_OK) {
        return ret;
    }

    *ps   = ((uint16_t)raw[1] << 8) | raw[0];
    *als0 = ((uint16_t)raw[3] << 8) | raw[2];
    *als1 = ((uint16_t)raw[5] << 8) | raw[4];

    return ESP_OK;
}

static float calc_lux(uint16_t als0, uint16_t als1)
{
    float lux = 0.0f;

    if (als0 == 0) {
        return 0.0f;
    }

    float ratio = (float)als1 / (float)als0;

    if (ratio < 0.595f) {
        lux = 1.682f * als0 - 1.877f * als1;
    } else if (ratio < 1.015f) {
        lux = 0.644f * als0 - 0.132f * als1;
    } else if (ratio < 1.352f) {
        lux = 0.756f * als0 - 0.243f * als1;
    } else if (ratio < 3.053f) {
        lux = 0.766f * als0 - 0.250f * als1;
    } else {
        lux = 0.0f;
    }

    return lux;
}

void app_main(void)
{
    esp_err_t ret;

    ret = i2c_init();
    if (ret != ESP_OK) {
        printf("RPR read error: %d\n", ret);
        return;
    }

    ret = rpr_init();
    if (ret != ESP_OK) {
        printf("RPR read error: %d\n", ret);
        return;
    }

    vTaskDelay(pdMS_TO_TICKS(100));

    while (1) {
        uint16_t ps;
        uint16_t als0;
        uint16_t als1;

        ret = rpr_read(&ps, &als0, &als1);

        if (ret == ESP_OK) {
            float lux = calc_lux(als0, als1);

            printf("lux = %.2f, ps = %u, als0 = %u, als1 = %u\n", lux, ps, als0, als1);
        } else {
            printf("RPR read error: %d\n", ret);
        }

        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}