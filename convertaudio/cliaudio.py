import argparse
import os
import sys
from pydub import AudioSegment

# List of common audio and video extensions to search for
SUPPORTED_INPUTS = (
    '.wav', '.mp3', '.ogg', '.flac', '.aac', '.m4a', '.wma', 
    '.mp4', '.avi', '.mkv', '.mov', '.webm', '.wmv'
)

def convert_audio(input_path, output_path, target_format="mp3", bitrate="128k"):
    try:
        # Use from_file to automatically detect input format (WAV, MP4, etc.)
        audio = AudioSegment.from_file(input_path)
        
        if target_format == "mp3":
            audio.export(output_path, format="mp3", bitrate=bitrate)
        elif target_format == "flac":
            audio.export(output_path, format="flac")
        elif target_format == "wav":
            audio.export(output_path, format="wav")
        elif target_format == "mp4":
            audio.export(output_path, format="mp4", bitrate=bitrate)
        
        return True, None
    except Exception as e:
        return False, str(e)

def main():
    parser = argparse.ArgumentParser(description="Universal Batch Converter (Any format to MP3/FLAC/WAV).")
    
    parser.add_argument("--input", required=True, help="Path to the folder containing audio/video files")
    parser.add_argument("--output", help="Path to save converted files (Default: input_folder/output)")
    parser.add_argument("--format", default="mp3", choices=["mp3", "flac", "wav", "mp4"], help="Target format")
    parser.add_argument("--bitrate", default="128k", help="Bitrate for MP3/MP4 (e.g., 64k, 192k)")

    args = parser.parse_args()

    input_folder = args.input
    output_folder = args.output if args.output else os.path.join(input_folder, "converted_output")
    
    if not os.path.exists(input_folder):
        print(f"Error: Input folder '{input_folder}' not found.")
        sys.exit(1)

    files_to_process = []
    print(f"Scanning '{input_folder}' for supported media files...")
    
    # os.walk handles recursive scanning
    for root, dirs, files in os.walk(input_folder):
        for file in files:
            if file.lower().endswith(SUPPORTED_INPUTS):
                files_to_process.append(os.path.join(root, file))
    
    if not files_to_process:
        print("No supported files found.")
        sys.exit(0)

    print(f"Found {len(files_to_process)} file(s). Starting conversion to {args.format.upper()}...\n")
    
    success_count = 0
    for in_path in files_to_process:
        try:
            # Calculate relative directory structure
            relative_dir = os.path.dirname(os.path.relpath(in_path, input_folder))
            out_dir = os.path.join(output_folder, relative_dir)
            os.makedirs(out_dir, exist_ok=True)

            # Create output filename (keeps original name, changes extension)
            filename = os.path.basename(in_path)
            name_only = os.path.splitext(filename)[0]
            output_filename = f"{name_only}.{args.format}"
            out_path = os.path.join(out_dir, output_filename)

            # Clean display path
            if relative_dir:
                display_path = os.path.join(relative_dir, filename)
            else:
                display_path = filename

            # Skip if output already exists (optional, prevents re-running)
            if os.path.exists(out_path):
                print(f"Skipping {display_path} (Already exists)")
                continue

            print(f"Processing {display_path}...", end=" ")
            success, error = convert_audio(in_path, out_path, args.format, args.bitrate)
            
            if success:
                print("DONE")
                success_count += 1
            else:
                print(f"FAILED ({error})")

        except Exception as e:
            print(f"CRITICAL ERROR: {e}")

    print(f"\nFinished. Success: {success_count}/{len(files_to_process)}")
    print(f"Output location: {os.path.abspath(output_folder)}")

if __name__ == "__main__":
    main()