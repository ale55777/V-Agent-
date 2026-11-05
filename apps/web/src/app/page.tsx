import { Suspense } from "react";
import { VoiceApp } from "@/components/VoiceApp";

export default function Home() {
  return (
    <Suspense>
      <VoiceApp />
    </Suspense>
  );
}
