/** Option lists shared by every client, so web and mobile offer the same choices. */
export const LANGUAGES = [
  { value: "en", label: "English" },
  { value: "ur", label: "Urdu (experimental: needs a larger AI model)" },
  { value: "auto", label: "Detect automatically" },
] as const;

export const ATTENTION_MODES = [
  { value: "always", label: "Always listening", help: "Responds to everything you say during the session." },
  { value: "wake_word", label: "Wake word", help: "Responds after you say the wake word, for 20 seconds." },
  { value: "manual", label: "Manual pause", help: "Listens until you press Pause, then ignores the mic until Resume." },
] as const;

export const VOICE_COMMANDS = ["Pause", "Stop", "Continue", "I'm back", "Listen to me", "Repeat that", "End conversation"] as const;
