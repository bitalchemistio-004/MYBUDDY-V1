#pragma once
/*
 * Speaker module for the mic_lab test firmware.
 *
 * Plays 16-bit mono PCM at 22.05 kHz through the robot's MAX98357A amplifier
 * (BCLK=5, LRC=4, DIN=6 — the same pins as production), on I2S port 1 so it
 * never touches the microphone driver on port 0.
 *
 * server -> device (binary):  'S' 'P' version flags seq_lo seq_hi  <int16 LE mono PCM>
 *                             flags bit0 = last chunk of this stream
 * server -> device (text):    "SPK reset"  flush and stop
 * device -> server (text):    "SPKDONE played=<bytes> under=<n> drop=<bytes>" when a stream has finished
 * STAT fields:                spkok spkplayed spkunder spkdrop spkbuf
 */
#include <Arduino.h>

bool labSpeakerBegin();
bool labSpeakerReady();
void labSpeakerOnBinary(const uint8_t* data, size_t len);
bool labSpeakerOnText(const char* msg);              // true if the message was a speaker command
size_t labSpeakerStat(char* out, size_t outLen);     // appends " spkok=.. spkplayed=.. ..." to out
bool labSpeakerTakeDone(char* out, size_t outLen);   // true once per finished stream
