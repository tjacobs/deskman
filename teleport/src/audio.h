/*
 * Call mic, speaker, echo cancel.
*/

#pragma once

#include <string>

#ifdef HAVE_GSTREAMER_WEBRTC
#include <gst/gst.h>
#endif

using namespace std;

void setVideoAudioDevice(string device);
void setVideoAudioDevices(string micDevice, string speakerDevice);
void setVideoMuteMic(bool muteMic);
void startRemotePcmPlayback();
void stopRemotePcmPlayback();
bool playRemotePcmPacket(const char* data, size_t size);
string findUsbAlsaDevice();
string findUsbMicDevice();
string findUsbSpeakerDevice();
string findUsbPulseSource();
string findUsbPulseSink();

#ifdef HAVE_GSTREAMER_WEBRTC
void quietCallAudioLog();
string createAudioSourceString();
void attachCallAudio(GstElement* pipeline, GMainLoop* loop);
void stopCallAudio();
void linkRemoteAudioPad(GstElement* pipeline, GstPad* pad);
bool isCallAudioDisconnectMessage(const string& errorMessage, const string& debugMessage);
bool shouldLogCallAudioDisconnect();
#endif
