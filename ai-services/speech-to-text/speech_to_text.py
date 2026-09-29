import whisper


print("Loading Whisper model...")

model = whisper.load_model("base")

print("Whisper model loaded successfully.")


def transcribe_audio(audio_file):
    """
    Convert an audio file into text using Whisper.
    """

    result = model.transcribe(audio_file)

    transcript = result["text"].strip()

    return transcript


if __name__ == "__main__":

    audio_file = "recordings/Recording.m4a"

    transcript = transcribe_audio(audio_file)

    print("\nTRANSCRIPT:")
    print(transcript)