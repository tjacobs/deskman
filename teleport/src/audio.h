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
void setCallMicMuted(bool muted);
void setCallMicMuteDefault(bool muted);
void applyCallMicMuteDefault();
bool isCallMicMuted();
void setCallAudioPayloadType(int payloadType);
int getCallAudioPayloadType();
void logCallAudioStatus();
void startRemotePCMPlayback();
void stopRemotePCMPlayback();
bool playRemotePCMPacket(const char* data, size_t size);
void startRingtone();
void stopRingtone();
string findUSBALSADevice();
string findUSBMicDevice();
string findUSBSpeakerDevice();
string findUSBPulseSource();
string findUSBPulseSink();

#ifdef HAVE_GSTREAMER_WEBRTC
void quietCallAudioLog();
string createAudioSourceString();
void attachCallAudio(GstElement* pipeline, GMainLoop* loop);
void stopCallAudio();
void linkRemoteAudioPad(GstElement* pipeline, GstPad* pad);
bool isCallAudioDisconnectMessage(const string& errorMessage, const string& debugMessage);
bool shouldLogCallAudioDisconnect();
#endif
