from huggingface_hub import AsyncInferenceClient

from config import config


class Transcribe:
    def __init__(self):
        self.HF_TOKEN = config.HF_INFERENCE_TOKEN
        self.STT_MODEL = config.STT_MODEL_NAME
        self.PROVIDER = "hf-inference"
        
    async def transcribe_audio(self, audio_path: str) -> str:
        print(
            f"[STT] Transcribing: "
            f"{audio_path}"
        )

        client = AsyncInferenceClient(
            provider=self.PROVIDER,
            api_key=self.HF_TOKEN
        )

        try:

            result = await client.automatic_speech_recognition(
                audio=audio_path,
                model=self.STT_MODEL
            )

            transcript = (
                result.text
                if hasattr(result, "text")
                else str(result)
            )

            transcript = transcript.strip()

            print(
                f"[STT] Transcript: "
                f"'{transcript}'"
            )

            return transcript

        finally:

            await client.close()

transcribe = Transcribe()