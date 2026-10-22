#ifndef COMMON_h
#define COMMON_h

#include <U8g2lib.h>

// Use SH1106 VCOMH0 memory mapping for 1.3" panels with 132-column controller RAM.
#ifndef MBUDDY_OLED_USE_SH1106_VCOMH0
#define MBUDDY_OLED_USE_SH1106_VCOMH0 1
#endif

#if MBUDDY_OLED_USE_SH1106_VCOMH0
typedef U8G2_SH1106_128X64_VCOMH0_F_HW_I2C MbuddyOled;
#else
typedef U8G2_SSD1306_128X64_VCOMH0_F_HW_I2C MbuddyOled;
#endif

extern MbuddyOled u8g2;


#endif
