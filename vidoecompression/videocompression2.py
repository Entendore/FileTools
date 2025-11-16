#!/usr/bin/env python3
import subprocess
import sys
import os
import argparse
import re
import json
import time
import signal
import math
from pathlib import Path

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

class VideoCompressor:
    RESOLUTION_PRESETS = {
        '144p': '256x144',
        '240p': '426x240',
        '360p': '640x360',
        '480p': '854x480',
        '720p': '1280x720',
        '1080p': '1920x1080'
    }

    def __init__(self, input_path, output_path, target_size_mb=950, resolution=None, verbose=False):
        self.input_path = Path(input_path).resolve()
        self.output_path = Path(output_path).resolve()
        self.target_size_mb = target_size_mb
        self.verbose = verbose
        self.resolution = self._normalize_resolution(resolution, target_size_mb)
        
        if self.resolution and not re.match(r'^\d+x\d+$', self.resolution):
            raise ValueError(f"Invalid resolution format: {self.resolution}. Use WIDTHxHEIGHT (e.g., 640x480)")

    def _normalize_resolution(self, res_str, target_size):
        """Select appropriate resolution based on target size"""
        if res_str in self.RESOLUTION_PRESETS:
            return self.RESOLUTION_PRESETS[res_str]
        
        # Auto-select resolution for small targets
        if not res_str:
            if target_size <= 5:
                return self.RESOLUTION_PRESETS['144p']
            elif target_size <= 15:
                return self.RESOLUTION_PRESETS['240p']
            elif target_size <= 50:
                return self.RESOLUTION_PRESETS['360p']
            elif target_size <= 100:
                return self.RESOLUTION_PRESETS['480p']
        
        return res_str

    def _parse_time(self, time_str):
        """Convert FFmpeg time string (HH:MM:SS.mmm) to seconds"""
        try:
            parts = time_str.split(':')
            hours = float(parts[0])
            minutes = float(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
        except (IndexError, ValueError):
            return 0.0

    def _get_stream_info(self):
        """Get accurate video duration and resolution using ffprobe"""
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(self.input_path)
        ]
        
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=True
            )
            duration = float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError) as e:
            raise RuntimeError(f"Failed to get video duration: {e.stderr.strip() if e.stderr else str(e)}") from e

        # Get original resolution
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height',
            '-of', 'csv=s=x:p=0',
            str(self.input_path)
        ]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=True
            )
            width, height = map(int, result.stdout.strip().split('x'))
            original_res = f"{width}x{height}"
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to get original resolution: {e.stderr.strip()}") from e

        return duration, original_res

    def _run_ffmpeg(self, cmd, duration, description):
        """Run FFmpeg command with progress tracking"""
        if self.verbose:
            print(f"\nExecuting: {' '.join(cmd)}")
            process = subprocess.Popen(cmd)
            process.wait()
            if process.returncode != 0:
                raise RuntimeError(f"FFmpeg failed with code {process.returncode}")
            return

        # Setup progress monitoring
        use_tqdm = TQDM_AVAILABLE and duration > 5
        pbar = None
        last_time = 0.0
        start_time = time.time()
        current_time = 0.0
        
        if use_tqdm and duration > 0:
            pbar = tqdm(
                total=duration,
                unit='s',
                desc=description,
                dynamic_ncols=True,
                bar_format='{l_bar}{bar}| {n:.1f}/{total:.1f}s [{elapsed}<{remaining}, {rate_fmt}{postfix}]'
            )
        else:
            print(f"{description} (duration: {duration:.1f}s)...")

        # Start FFmpeg process
        process = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            preexec_fn=os.setsid if os.name != 'nt' else None
        )

        # Monitor progress output
        try:
            while True:
                line = process.stderr.readline()
                if not line:
                    break
                
                # Parse time updates from FFmpeg
                if 'time=' in line:
                    time_match = re.search(r'time=(\d+:\d+:\d+\.\d+)', line)
                    if time_match:
                        current_time = self._parse_time(time_match.group(1))
                        
                        if use_tqdm and pbar:
                            pbar.n = min(current_time, duration)
                            elapsed = time.time() - start_time
                            if elapsed > 0:
                                speed = current_time / elapsed
                                pbar.set_postfix(speed=f"{speed:.2f}x")
                            pbar.refresh()
                        elif time.time() - last_time >= 1.0:  # Update every second
                            elapsed = time.time() - start_time
                            speed = current_time / elapsed if elapsed > 0 else 0
                            progress = min(100, int(current_time / duration * 100)) if duration > 0 else 0
                            print(f"{description}: {current_time:.1f}/{duration:.1f}s ({progress}%) [{speed:.2f}x]", end='\r')
                            last_time = time.time()
        except KeyboardInterrupt:
            print("\nInterrupting FFmpeg process...")
            if os.name == 'nt':
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)], stderr=subprocess.DEVNULL)
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            raise
        finally:
            process.wait(timeout=10)
            if use_tqdm and pbar:
                pbar.close()
            elif not self.verbose:
                print()  # Newline after progress updates

        if process.returncode not in (0, 255):  # 255 sometimes on Windows interrupt
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"FFmpeg failed with code {process.returncode}\n{stderr.strip()}")

    def compress(self):
        """Compress video using two-pass encoding for accurate file size targeting"""
        try:
            duration, original_res = self._get_stream_info()
            if duration <= 0:
                raise ValueError("Invalid video duration detected")
        except Exception as e:
            print(f"Error: {str(e)}")
            sys.exit(1)

        print(f"Source video: {original_res} @ {duration:.1f} seconds")
        print(f"Target size: {self.target_size_mb} MB")
        
        # Determine resolution
        target_res = self.resolution
        if not target_res:
            target_res = self._normalize_resolution(None, self.target_size_mb)
            print(f"Auto-selected resolution for {self.target_size_mb}MB target: {target_res}")
        else:
            print(f"Using specified resolution: {target_res}")

        # Calculate bitrates with overhead estimation
        total_bits = self.target_size_mb * 8388608  # bits (1024*1024*8)
        
        # Audio bitrate selection (lower for small files)
        audio_bitrate_kbps = 64 if self.target_size_mb <= 10 else 96 if self.target_size_mb <= 50 else 128
        audio_bitrate_bps = audio_bitrate_kbps * 1000
        
        # Estimate container overhead (more significant for small files)
        overhead_percent = 0.05 if self.target_size_mb <= 20 else 0.03 if self.target_size_mb <= 50 else 0.01
        overhead_bits = total_bits * overhead_percent
        
        # Calculate available bits for video
        audio_total_bits = audio_bitrate_bps * duration
        video_total_bits = total_bits - audio_total_bits - overhead_bits
        
        # Safety checks for minimum bitrates
        MIN_VIDEO_BITRATE = 50000  # 50 kbps minimum
        min_video_bits = MIN_VIDEO_BITRATE * duration
        if video_total_bits < min_video_bits:
            print(f"WARNING: Target size too small! Using minimum video bitrate of {MIN_VIDEO_BITRATE//1000} kbps")
            video_total_bits = min_video_bits
            # Recalculate actual expected size
            actual_bits = video_total_bits + audio_total_bits + overhead_bits
            actual_size_mb = actual_bits / 8388608
            print(f"Adjusted target size: {actual_size_mb:.1f} MB (was {self.target_size_mb} MB)")

        video_bitrate_bps = video_total_bits / duration
        video_bitrate_kbps = int(video_bitrate_bps / 1000)  # Convert to kbps
        
        print(f"Calculated bitrates - Video: {video_bitrate_kbps} kbps, Audio: {audio_bitrate_kbps} kbps")
        print(f"Container overhead estimate: {overhead_percent:.0%} ({overhead_bits/8388608:.2f} MB)")

        # Build base FFmpeg command
        cmd_base = [
            'ffmpeg',
            '-y',  # Overwrite output
            '-i', str(self.input_path),
            '-c:v', 'libx264',
            '-preset', 'slow',
            '-profile:v', 'baseline' if self.target_size_mb <= 50 else 'main',
            '-pix_fmt', 'yuv420p',  # Max compatibility
            '-movflags', '+faststart',  # Web optimization
        ]

        # Add resolution scaling if needed
        if target_res != original_res:
            width, height = map(int, target_res.split('x'))
            original_w, original_h = map(int, original_res.split('x'))
            
            # Maintain aspect ratio with padding
            scale_cmd = (
                f"scale='min({width},iw*{height}/ih)':'min({height},ih*{width}/iw)',"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
            )
            cmd_base.extend(['-vf', scale_cmd])

        # First pass command (analysis only)
        pass1_cmd = cmd_base + [
            '-b:v', f'{video_bitrate_kbps}k',
            '-pass', '1',
            '-passlogfile', 'ffmpeg2pass',
            '-an',  # Disable audio for first pass
            '-f', 'null',
            'NUL' if os.name == 'nt' else '/dev/null'
        ]

        # Second pass command (actual encoding)
        pass2_cmd = cmd_base + [
            '-b:v', f'{video_bitrate_kbps}k',
            '-maxrate:v', f'{video_bitrate_kbps * 2}k',
            '-bufsize:v', f'{video_bitrate_kbps * 4}k',
            '-pass', '2',
            '-passlogfile', 'ffmpeg2pass',
            '-c:a', 'aac',
            '-b:a', f'{audio_bitrate_kbps}k',
            str(self.output_path)
        ]

        # Execute two-pass encoding
        try:
            # First pass
            self._run_ffmpeg(pass1_cmd, duration, "Analysis Pass")
            
            # Second pass
            self._run_ffmpeg(pass2_cmd, duration, "Encoding Pass")
            
            # Cleanup passlog files
            for ext in ['', '.mbtree']:
                logfile = Path(f'ffmpeg2pass{ext}')
                if logfile.exists():
                    logfile.unlink()
        except Exception as e:
            # Cleanup on failure
            for ext in ['', '.mbtree']:
                logfile = Path(f'ffmpeg2pass{ext}')
                if logfile.exists():
                    logfile.unlink()
            raise

        # Verify output
        if not self.output_path.exists():
            raise RuntimeError("Output file was not created")
        
        final_size_mb = self.output_path.stat().st_size / (1024 * 1024)
        print(f"\nCompression completed! Final size: {final_size_mb:.2f} MB")
        print(f"Output saved to: {self.output_path}")

        # Size accuracy report
        size_diff = abs(final_size_mb - self.target_size_mb)
        size_accuracy = 100 - (size_diff / self.target_size_mb * 100)
        print(f"Size accuracy: {size_accuracy:.1f}% of target")
        
        if size_diff > self.target_size_mb * 0.1:  # More than 10% off
            print(f"WARNING: File size {(size_diff):.1f}MB over target. Consider:")
            print(f"- Reducing resolution further (current: {target_res})")
            print(f"- Lowering target size parameter to compensate for overhead")
            print(f"- Using --verbose mode to diagnose encoding issues")

def check_dependencies():
    """Verify FFmpeg and FFprobe are available"""
    for tool in ['ffmpeg', 'ffprobe']:
        if not shutil.which(tool):
            print(f"ERROR: {tool} not found in PATH. Please install FFmpeg first.")
            print("Download: https://ffmpeg.org/download.html")
            sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description='Compress video to exact target file size')
    parser.add_argument('input', help='Input video file path')
    parser.add_argument('output', help='Output video file path')
    parser.add_argument('--target-size', type=int, default=950,
                        help='Target file size in MB (default: 950)')
    parser.add_argument('--resolution', 
                        help='Target resolution (WIDTHxHEIGHT or preset: 144p/240p/360p/480p/720p/1080p)')
    parser.add_argument('--verbose', action='store_true',
                        help='Show detailed FFmpeg output')
    parser.add_argument('--force', action='store_true',
                        help='Overwrite output file without confirmation')
    
    args = parser.parse_args()

    # Validate dependencies first
    check_dependencies()
    
    # Validate input file
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}")
        sys.exit(1)
    
    # Handle output file overwrite
    output_path = Path(args.output).resolve()
    if output_path.exists() and not args.force:
        response = input(f"Output file '{output_path}' exists. Overwrite? [y/N] ")
        if response.lower() != 'y':
            print("Aborted by user.")
            sys.exit(1)

    # Check for tqdm
    if not TQDM_AVAILABLE and not args.verbose:
        print("TIP: Install tqdm for beautiful progress bars: pip install tqdm")

    try:
        compressor = VideoCompressor(
            input_path=input_path,
            output_path=output_path,
            target_size_mb=args.target_size,
            resolution=args.resolution,
            verbose=args.verbose
        )
        compressor.compress()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user (Ctrl+C)")
        sys.exit(130)
    except Exception as e:
        print(f"\nCRITICAL ERROR: {str(e)}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        # Cleanup potential partial output
        if output_path.exists() and output_path.stat().st_size == 0:
            output_path.unlink()
        sys.exit(1)

if __name__ == "__main__":
    import shutil
    main()