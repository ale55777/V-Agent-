# EchoAI mobile (future Expo app)

This folder is **scaffolding, not a working app**. Nothing here has been built or run on a device.

## What is already ready

The backend needs no changes for mobile. A phone app uses the same:

- REST API and WebSocket protocol (`packages/shared-types`, documented in the root README)
- REST client (`packages/api-client`, which only needs `fetch`)
- Option lists (`packages/agent-config`)
- Ollama, Whisper, TTS, database, agent settings, memory and history
- Device handoff: `POST /api/v1/sessions/{id}/handoff`, then a new socket. One device owns the
  microphone at a time. The old audio link is closed, not transferred.

`src/voiceConnection.ts` holds the platform-neutral socket logic. It takes the audio layer as an
interface, so the hard part below stays separate.

## The audio question

The protocol needs continuous PCM16 16 kHz microphone frames and low-latency PCM playback that can
be stopped instantly. As far as I know:

- `expo-av` and `expo-audio` record to files, not to a stream of frames, so they do not fit.
- Streaming needs a native module, such as `react-native-audio-api` or `@siteed/expo-audio-studio`.
- **Expo Go cannot load extra native modules**, so plan on an
  [Expo development build](https://docs.expo.dev/develop/development-builds/introduction/).
  `eas.json` here is set up for one.

**None of this is verified.** Before relying on it, confirm on a real device that:

1. the chosen module delivers frames while audio is playing (full duplex)
2. hardware echo cancellation is active (iOS `voiceChat` audio session mode, Android
   `VOICE_COMMUNICATION` source), since barge-in depends on it
3. it works with the Expo SDK current at that time

## Connecting to the PC

Never hardcode `localhost`: on a phone, that means the phone. Set `EXPO_PUBLIC_API_URL`:

```
EXPO_PUBLIC_API_URL=https://YOUR_PC_LAN_IP:8000
```

Native apps refuse self-signed certificates unless the certificate is installed on the device. For
development, use plain `http://` with cleartext enabled in a dev build, or trust
`.data/certs/echoai.crt` on the device.

## Starting the app later

```powershell
npx create-expo-app@latest apps/mobile-app --template blank-typescript
# Then bring in this folder's src/, add "apps/mobile" to the root package.json workspaces,
# and implement the MicSource and SpeechOutput interfaces with the chosen native module.
```
