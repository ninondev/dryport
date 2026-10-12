// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
#include <stdint.h>
#include <string.h>
typedef uintptr_t U;
int FMOD5_System_Create(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a0) *(uint64_t*)a0=(uint64_t)1; return 0; }
int FMOD5_Memory_Initialize(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Memory_GetStats(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a0) *(uint32_t*)a0=0; if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Debug_Initialize(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Thread_SetAttributes(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_Release(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_SetOutput(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetOutput(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_System_GetNumDrivers(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_System_GetDriverInfo(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a5) *(uint32_t*)a5=0; if(a6) *(uint32_t*)a6=0; if(a7) *(uint32_t*)a7=0; return 0; }
int FMOD5_System_SetDriver(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetDriver(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_System_SetSoftwareChannels(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetSoftwareChannels(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_System_SetSoftwareFormat(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetSoftwareFormat(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_System_SetDSPBufferSize(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetDSPBufferSize(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_System_SetFileSystem(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_AttachFileSystem(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_SetAdvancedSettings(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetAdvancedSettings(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_SetCallback(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_SetPluginPath(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_LoadPlugin(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_System_UnloadPlugin(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetNumNestedPlugins(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_System_GetNestedPlugin(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_System_GetNumPlugins(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_System_GetPluginHandle(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_System_GetPluginInfo(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; if(a5) *(uint32_t*)a5=0; return 0; }
int FMOD5_System_SetOutputByPlugin(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetOutputByPlugin(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_System_CreateDSPByPlugin(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_System_GetDSPInfoByPlugin(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_System_RegisterDSP(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_System_Init(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_Close(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_Update(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_SetSpeakerPosition(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetSpeakerPosition(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; if(a4) *(uint32_t*)a4=0; return 0; }
int FMOD5_System_SetStreamBufferSize(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetStreamBufferSize(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_System_Set3DSettings(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_Get3DSettings(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_System_Set3DNumListeners(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_Get3DNumListeners(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_System_Set3DListenerAttributes(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_Get3DListenerAttributes(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_Set3DRolloffCallback(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_MixerSuspend(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_MixerResume(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetDefaultMixMatrix(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetSpeakerModeChannels(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_System_GetVersion(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0x00020207u; return 0; }
int FMOD5_System_GetOutputHandle(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_System_GetChannelsPlaying(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_System_GetCPUUsage(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetFileUsage(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; if(a2) *(uint64_t*)a2=(uint64_t)1; if(a3) *(uint64_t*)a3=(uint64_t)1; return 0; }
int FMOD5_System_CreateSound(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a4) *(uint64_t*)a4=(uint64_t)1; return 0; }
int FMOD5_System_CreateStream(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a4) *(uint64_t*)a4=(uint64_t)1; return 0; }
int FMOD5_System_CreateDSP(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_System_CreateDSPByType(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_System_CreateChannelGroup(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_System_CreateSoundGroup(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_System_CreateReverb3D(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_System_PlaySound(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a4) *(uint64_t*)a4=(uint64_t)1; return 0; }
int FMOD5_System_PlayDSP(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a4) *(uint64_t*)a4=(uint64_t)1; return 0; }
int FMOD5_System_GetChannel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_System_GetDSPInfoByType(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_System_GetMasterChannelGroup(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_System_GetMasterSoundGroup(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_System_AttachChannelGroupToPort(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_DetachChannelGroupFromPort(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_SetReverbProperties(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetReverbProperties(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_LockDSP(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_UnlockDSP(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetRecordNumDrivers(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_System_GetRecordDriverInfo(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetRecordPosition(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_System_RecordStart(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_RecordStop(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_IsRecording(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_System_CreateGeometry(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint64_t*)a3=(uint64_t)1; return 0; }
int FMOD5_System_SetGeometrySettings(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetGeometrySettings(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_System_LoadGeometry(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint64_t*)a3=(uint64_t)1; return 0; }
int FMOD5_System_GetGeometryOcclusion(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; if(a4) *(uint32_t*)a4=0; return 0; }
int FMOD5_System_SetNetworkProxy(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetNetworkProxy(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_SetNetworkTimeout(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetNetworkTimeout(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_System_SetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_System_GetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_Sound_Release(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_GetSystemObject(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_Sound_Lock(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint64_t*)a3=(uint64_t)1; if(a4) *(uint64_t*)a4=(uint64_t)1; if(a5) *(uint32_t*)a5=0; if(a6) *(uint32_t*)a6=0; return 0; }
int FMOD5_Sound_Unlock(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_SetDefaults(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_GetDefaults(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_Sound_Set3DMinMaxDistance(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_Get3DMinMaxDistance(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_Sound_Set3DConeSettings(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_Get3DConeSettings(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_Sound_Set3DCustomRolloff(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_Get3DCustomRolloff(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_Sound_GetSubSound(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_Sound_GetSubSoundParent(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_Sound_GetName(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_GetLength(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Sound_GetFormat(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; if(a4) *(uint32_t*)a4=0; return 0; }
int FMOD5_Sound_GetNumSubSounds(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Sound_GetNumTags(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_Sound_GetTag(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_GetOpenState(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; if(a4) *(uint32_t*)a4=0; return 0; }
int FMOD5_Sound_ReadData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_Sound_SeekData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_SetSoundGroup(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_GetSoundGroup(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_Sound_GetNumSyncPoints(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Sound_GetSyncPoint(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_Sound_GetSyncPointInfo(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a4) *(uint32_t*)a4=0; return 0; }
int FMOD5_Sound_AddSyncPoint(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a4) *(uint64_t*)a4=(uint64_t)1; return 0; }
int FMOD5_Sound_DeleteSyncPoint(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_SetMode(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_GetMode(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Sound_SetLoopCount(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_GetLoopCount(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Sound_SetLoopPoints(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_GetLoopPoints(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_Sound_GetMusicNumChannels(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Sound_SetMusicChannelVolume(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_GetMusicChannelVolume(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_Sound_SetMusicSpeed(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_GetMusicSpeed(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Sound_SetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Sound_GetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_Channel_SetFrequency(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetFrequency(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_SetPriority(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetPriority(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_SetPosition(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetPosition(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_SetChannelGroup(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetChannelGroup(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_Channel_SetLoopCount(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetLoopCount(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_SetLoopPoints(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetLoopPoints(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_Channel_IsVirtual(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_GetCurrentSound(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_Channel_GetIndex(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_GetSystemObject(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_Channel_Stop(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_SetPaused(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetPaused(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_SetVolume(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetVolume(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_SetVolumeRamp(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetVolumeRamp(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_GetAudibility(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_SetPitch(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetPitch(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_SetMute(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetMute(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_SetReverbProperties(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetReverbProperties(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_Channel_SetLowPassGain(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetLowPassGain(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_SetMode(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetMode(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_SetCallback(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_IsPlaying(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_SetPan(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_SetMixLevelsOutput(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_SetMixLevelsInput(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_SetMixMatrix(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetMixMatrix(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_Channel_GetDSPClock(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_Channel_SetDelay(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetDelay(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_Channel_AddFadePoint(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_SetFadePointRamp(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_RemoveFadePoints(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetFadePoints(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_GetDSP(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_Channel_AddDSP(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_RemoveDSP(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetNumDSPs(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_SetDSPIndex(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetDSPIndex(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_Channel_Set3DAttributes(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_Get3DAttributes(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_Set3DMinMaxDistance(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_Get3DMinMaxDistance(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_Channel_Set3DConeSettings(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_Get3DConeSettings(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_Channel_Set3DConeOrientation(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_Get3DConeOrientation(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_Set3DCustomRolloff(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_Get3DCustomRolloff(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_Channel_Set3DOcclusion(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_Get3DOcclusion(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_Channel_Set3DSpread(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_Get3DSpread(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_Set3DLevel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_Get3DLevel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_Set3DDopplerLevel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_Get3DDopplerLevel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Channel_Set3DDistanceFilter(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_Get3DDistanceFilter(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_Channel_SetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Channel_GetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_ChannelGroup_Release(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_AddGroup(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetNumGroups(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_GetGroup(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_ChannelGroup_GetParentGroup(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_ChannelGroup_GetName(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetNumChannels(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_GetChannel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_ChannelGroup_GetSystemObject(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_ChannelGroup_Stop(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_SetPaused(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetPaused(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_SetVolume(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetVolume(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_SetVolumeRamp(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetVolumeRamp(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_GetAudibility(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_SetPitch(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetPitch(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_SetMute(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetMute(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_SetReverbProperties(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetReverbProperties(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_ChannelGroup_SetLowPassGain(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetLowPassGain(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_SetMode(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetMode(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_SetCallback(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_IsPlaying(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_SetPan(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_SetMixLevelsOutput(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_SetMixLevelsInput(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_SetMixMatrix(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetMixMatrix(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_ChannelGroup_GetDSPClock(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_ChannelGroup_SetDelay(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetDelay(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_ChannelGroup_AddFadePoint(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_SetFadePointRamp(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_RemoveFadePoints(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetFadePoints(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_GetDSP(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_ChannelGroup_AddDSP(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_RemoveDSP(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetNumDSPs(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_SetDSPIndex(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetDSPIndex(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_ChannelGroup_Set3DAttributes(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_Get3DAttributes(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_Set3DMinMaxDistance(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_Get3DMinMaxDistance(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_ChannelGroup_Set3DConeSettings(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_Get3DConeSettings(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_ChannelGroup_Set3DConeOrientation(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_Get3DConeOrientation(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_Set3DCustomRolloff(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_Get3DCustomRolloff(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_ChannelGroup_Set3DOcclusion(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_Get3DOcclusion(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_ChannelGroup_Set3DSpread(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_Get3DSpread(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_Set3DLevel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_Get3DLevel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_Set3DDopplerLevel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_Get3DDopplerLevel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_ChannelGroup_Set3DDistanceFilter(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_Get3DDistanceFilter(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_ChannelGroup_SetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_ChannelGroup_GetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_SoundGroup_Release(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_SoundGroup_GetSystemObject(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_SoundGroup_SetMaxAudible(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_SoundGroup_GetMaxAudible(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_SoundGroup_SetMaxAudibleBehavior(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_SoundGroup_GetMaxAudibleBehavior(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_SoundGroup_SetMuteFadeSpeed(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_SoundGroup_GetMuteFadeSpeed(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_SoundGroup_SetVolume(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_SoundGroup_GetVolume(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_SoundGroup_Stop(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_SoundGroup_GetName(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_SoundGroup_GetNumSounds(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_SoundGroup_GetSound(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_SoundGroup_GetNumPlaying(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_SoundGroup_SetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_SoundGroup_GetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_DSP_Release(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_GetSystemObject(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_DSP_AddInput(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_DisconnectFrom(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_DisconnectAll(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_GetNumInputs(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_DSP_GetNumOutputs(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_DSP_GetInput(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; if(a3) *(uint64_t*)a3=(uint64_t)1; return 0; }
int FMOD5_DSP_GetOutput(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; if(a3) *(uint64_t*)a3=(uint64_t)1; return 0; }
int FMOD5_DSP_SetActive(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_GetActive(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_DSP_SetBypass(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_GetBypass(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_DSP_SetWetDryMix(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_GetWetDryMix(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_DSP_SetChannelFormat(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_GetChannelFormat(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_DSP_GetOutputChannelFormat(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a4) *(uint32_t*)a4=0; if(a5) *(uint32_t*)a5=0; if(a6) *(uint32_t*)a6=0; return 0; }
int FMOD5_DSP_Reset(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_SetParameterFloat(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_SetParameterInt(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_SetParameterBool(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_SetParameterData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_GetParameterFloat(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_DSP_GetParameterInt(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_DSP_GetParameterBool(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_DSP_GetParameterData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_DSP_GetNumParameters(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_DSP_GetParameterInfo(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD5_DSP_GetDataParameterIndex(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_DSP_ShowConfigDialog(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_GetInfo(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; if(a4) *(uint32_t*)a4=0; if(a5) *(uint32_t*)a5=0; return 0; }
int FMOD5_DSP_GetType(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_DSP_GetIdle(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_DSP_SetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_GetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_DSP_SetMeteringEnabled(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_GetMeteringEnabled(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_DSP_GetMeteringInfo(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSP_GetCPUUsage(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_DSPConnection_GetInput(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_DSPConnection_GetOutput(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_DSPConnection_SetMix(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSPConnection_GetMix(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_DSPConnection_SetMixMatrix(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSPConnection_GetMixMatrix(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_DSPConnection_GetType(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_DSPConnection_SetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_DSPConnection_GetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_Geometry_Release(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Geometry_AddPolygon(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a4) *(uint32_t*)a4=0; return 0; }
int FMOD5_Geometry_GetNumPolygons(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Geometry_GetMaxPolygons(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_Geometry_GetPolygonNumVertices(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_Geometry_SetPolygonVertex(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Geometry_GetPolygonVertex(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Geometry_SetPolygonAttributes(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Geometry_GetPolygonAttributes(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; if(a4) *(uint32_t*)a4=0; return 0; }
int FMOD5_Geometry_SetActive(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Geometry_GetActive(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Geometry_SetRotation(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Geometry_GetRotation(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Geometry_SetPosition(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Geometry_GetPosition(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Geometry_SetScale(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Geometry_GetScale(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Geometry_Save(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD5_Geometry_SetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Geometry_GetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD5_Reverb3D_Release(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Reverb3D_Set3DAttributes(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Reverb3D_Get3DAttributes(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD5_Reverb3D_SetProperties(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Reverb3D_GetProperties(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Reverb3D_SetActive(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Reverb3D_GetActive(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD5_Reverb3D_SetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD5_Reverb3D_GetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD_Studio_ParseID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_Create(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a0) *(uint64_t*)a0=(uint64_t)1; return 0; }
int FMOD_Studio_System_IsValid(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 1; }
int FMOD_Studio_System_SetAdvancedSettings(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_GetAdvancedSettings(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_Initialize(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_Release(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_Update(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_GetCoreSystem(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD_Studio_System_GetEvent(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD_Studio_System_GetBus(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD_Studio_System_GetVCA(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD_Studio_System_GetBank(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD_Studio_System_GetEventByID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD_Studio_System_GetBusByID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD_Studio_System_GetVCAByID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD_Studio_System_GetBankByID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint64_t*)a2=(uint64_t)1; return 0; }
int FMOD_Studio_System_GetSoundInfo(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_GetParameterDescriptionByName(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_GetParameterDescriptionByID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_GetParameterLabelByName(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a5) *(uint32_t*)a5=0; return 0; }
int FMOD_Studio_System_GetParameterLabelByID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a6) *(uint32_t*)a6=0; return 0; }
int FMOD_Studio_System_GetParameterByID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; if(a4) *(uint32_t*)a4=0; return 0; }
int FMOD_Studio_System_SetParameterByID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_SetParameterByIDWithLabel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_SetParametersByIDs(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_GetParameterByName(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD_Studio_System_SetParameterByName(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_SetParameterByNameWithLabel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_LookupID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_LookupPath(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a4) *(uint32_t*)a4=0; return 0; }
int FMOD_Studio_System_GetNumListeners(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_System_SetNumListeners(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_GetListenerAttributes(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_SetListenerAttributes(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_GetListenerWeight(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD_Studio_System_SetListenerWeight(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_LoadBankFile(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint64_t*)a3=(uint64_t)1; return 0; }
int FMOD_Studio_System_LoadBankMemory(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a5) *(uint64_t*)a5=(uint64_t)1; return 0; }
int FMOD_Studio_System_LoadBankCustom(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint64_t*)a3=(uint64_t)1; return 0; }
int FMOD_Studio_System_UnloadAll(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_FlushCommands(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_FlushSampleLoading(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_StartCommandCapture(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_StopCommandCapture(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_LoadCommandReplay(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint64_t*)a3=(uint64_t)1; return 0; }
int FMOD_Studio_System_GetBankCount(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_System_GetBankList(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD_Studio_System_GetParameterDescriptionCount(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_System_GetParameterDescriptionList(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD_Studio_System_GetCPUUsage(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_GetBufferUsage(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_ResetBufferUsage(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_SetCallback(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_GetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD_Studio_System_SetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_System_GetMemoryUsage(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventDescription_IsValid(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 1; }
int FMOD_Studio_EventDescription_GetID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventDescription_GetPath(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD_Studio_EventDescription_GetParameterDescriptionCount(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventDescription_GetParameterDescriptionByIndex(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventDescription_GetParameterDescriptionByName(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventDescription_GetParameterDescriptionByID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventDescription_GetParameterLabelByIndex(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a5) *(uint32_t*)a5=0; return 0; }
int FMOD_Studio_EventDescription_GetParameterLabelByName(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a5) *(uint32_t*)a5=0; return 0; }
int FMOD_Studio_EventDescription_GetParameterLabelByID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a6) *(uint32_t*)a6=0; return 0; }
int FMOD_Studio_EventDescription_GetUserPropertyCount(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventDescription_GetUserPropertyByIndex(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventDescription_GetUserProperty(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventDescription_GetLength(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventDescription_GetMinMaxDistance(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD_Studio_EventDescription_GetSoundSize(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventDescription_IsSnapshot(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventDescription_IsOneshot(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventDescription_IsStream(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventDescription_Is3D(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventDescription_IsDopplerEnabled(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventDescription_HasSustainPoint(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventDescription_CreateInstance(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD_Studio_EventDescription_GetInstanceCount(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventDescription_GetInstanceList(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD_Studio_EventDescription_LoadSampleData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventDescription_UnloadSampleData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventDescription_GetSampleLoadingState(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventDescription_ReleaseAllInstances(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventDescription_SetCallback(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventDescription_GetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD_Studio_EventDescription_SetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_IsValid(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 1; }
int FMOD_Studio_EventInstance_GetDescription(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD_Studio_EventInstance_GetVolume(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventInstance_SetVolume(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_GetPitch(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventInstance_SetPitch(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_Get3DAttributes(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_Set3DAttributes(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_GetListenerMask(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventInstance_SetListenerMask(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_GetProperty(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD_Studio_EventInstance_SetProperty(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_GetReverbLevel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD_Studio_EventInstance_SetReverbLevel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_GetPaused(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventInstance_SetPaused(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_Start(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_Stop(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_GetTimelinePosition(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventInstance_SetTimelinePosition(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_GetPlaybackState(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=2; return 0; }
int FMOD_Studio_EventInstance_GetChannelGroup(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD_Studio_EventInstance_GetMinMaxDistance(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD_Studio_EventInstance_Release(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_IsVirtual(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_EventInstance_GetParameterByName(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a2) *(uint32_t*)a2=0; if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD_Studio_EventInstance_SetParameterByName(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_SetParameterByNameWithLabel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_GetParameterByID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; if(a4) *(uint32_t*)a4=0; return 0; }
int FMOD_Studio_EventInstance_SetParameterByID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_SetParameterByIDWithLabel(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_SetParametersByIDs(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_KeyOff(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_SetCallback(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_GetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD_Studio_EventInstance_SetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_EventInstance_GetCPUUsage(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD_Studio_EventInstance_GetMemoryUsage(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_Bus_IsValid(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 1; }
int FMOD_Studio_Bus_GetID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_Bus_GetPath(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD_Studio_Bus_GetVolume(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD_Studio_Bus_SetVolume(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_Bus_GetPaused(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_Bus_SetPaused(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_Bus_GetMute(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_Bus_SetMute(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_Bus_StopAllEvents(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_Bus_LockChannelGroup(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_Bus_UnlockChannelGroup(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_Bus_GetChannelGroup(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD_Studio_Bus_GetCPUUsage(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD_Studio_Bus_GetMemoryUsage(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_Bus_GetPortIndex(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD_Studio_Bus_SetPortIndex(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_VCA_IsValid(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 1; }
int FMOD_Studio_VCA_GetID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_VCA_GetPath(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD_Studio_VCA_GetVolume(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD_Studio_VCA_SetVolume(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_Bank_IsValid(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 1; }
int FMOD_Studio_Bank_GetID(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_Bank_GetPath(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD_Studio_Bank_Unload(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_Bank_LoadSampleData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_Bank_UnloadSampleData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_Bank_GetLoadingState(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_Bank_GetSampleLoadingState(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_Bank_GetStringCount(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_Bank_GetStringInfo(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a5) *(uint32_t*)a5=0; return 0; }
int FMOD_Studio_Bank_GetEventCount(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_Bank_GetEventList(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD_Studio_Bank_GetBusCount(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_Bank_GetBusList(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD_Studio_Bank_GetVCACount(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_Bank_GetVCAList(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a3) *(uint32_t*)a3=0; return 0; }
int FMOD_Studio_Bank_GetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD_Studio_Bank_SetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_CommandReplay_IsValid(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 1; }
int FMOD_Studio_CommandReplay_GetSystem(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD_Studio_CommandReplay_GetLength(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_CommandReplay_GetCommandCount(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_CommandReplay_GetCommandInfo(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_CommandReplay_GetCommandString(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_CommandReplay_GetCommandAtTime(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_CommandReplay_SetBankPath(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_CommandReplay_Start(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_CommandReplay_Stop(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_CommandReplay_SeekToTime(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_CommandReplay_SeekToCommand(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_CommandReplay_GetPaused(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; return 0; }
int FMOD_Studio_CommandReplay_SetPaused(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_CommandReplay_GetPlaybackState(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=2; return 0; }
int FMOD_Studio_CommandReplay_GetCurrentCommand(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint32_t*)a1=0; if(a2) *(uint32_t*)a2=0; return 0; }
int FMOD_Studio_CommandReplay_Release(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_CommandReplay_SetFrameCallback(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_CommandReplay_SetLoadBankCallback(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_CommandReplay_SetCreateInstanceCallback(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
int FMOD_Studio_CommandReplay_GetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){ if(a1) *(uint64_t*)a1=(uint64_t)1; return 0; }
int FMOD_Studio_CommandReplay_SetUserData(U a0,U a1,U a2,U a3,U a4,U a5,U a6,U a7){  return 0; }
