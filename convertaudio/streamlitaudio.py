import os
import streamlit as st
from pydub import AudioSegment

st.set_page_config(page_title="Universal Audio Converter", layout="wide")
st.title("🎧 Universal Audio/Video Converter")
st.markdown("Convert MP4, WAV, FLAC, etc. to MP3 (or other formats). Works recursively.")

# Sidebar
with st.sidebar:
    st.header("Settings")
    
    target_format = st.selectbox("Target Format", ["mp3", "flac", "wav", "mp4"], index=0)
    
    bitrate = "128k"
    if target_format in ["mp3", "mp4"]:
        bitrate = st.select_slider(f"{target_format.upper()} Quality", 
                                   options=["32k", "64k", "96k", "128k", "192k", "320k"], value="128k")
    
    st.info(f"Config: `{target_format}`" + (f" @ `{bitrate}`" if target_format in ["mp3", "mp4"] else ""))

# CHANGED: List [] to Tuple () to fix TypeError
UPLOAD_TYPES = ("wav", "mp3", "mp4", "flac", "aac", "m4a", "ogg", "wma", "avi", "mkv", "mov")

# Tabs
tab1, tab2 = st.tabs(["Process Folder (Recursive)", "Upload Files"])

with tab1:
    st.subheader("Local Folder (Recursive)")
    folder_path = st.text_input("Enter path to folder", placeholder="/path/to/media")
    process_btn = st.button("Start Conversion", type="primary")

    if process_btn and folder_path:
        if not os.path.exists(folder_path):
            st.error(f"Folder not found: {folder_path}")
        else:
            output_root = os.path.join(folder_path, "web_output")
            os.makedirs(output_root, exist_ok=True)
            
            files_to_process = []
            # Recursive scan
            for root, dirs, files in os.walk(folder_path):
                for file in files:
                    # Fixed: UPLOAD_TYPES is now a tuple, so .endswith works
                    if file.lower().endswith(UPLOAD_TYPES):
                        files_to_process.append(os.path.join(root, file))
            
            if not files_to_process:
                st.warning("No supported files found.")
            else:
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                for i, in_path in enumerate(files_to_process):
                    relative_dir = os.path.dirname(os.path.relpath(in_path, folder_path))
                    out_dir = os.path.join(output_root, relative_dir)
                    os.makedirs(out_dir, exist_ok=True)
                    
                    base_name = os.path.splitext(os.path.basename(in_path))[0]
                    ext = target_format.replace(" (optimized)", "")
                    output_filename = f"{base_name}.{ext}"
                    out_path = os.path.join(out_dir, output_filename)
                    
                    display_path = os.path.join(relative_dir, os.path.basename(in_path)) if relative_dir else os.path.basename(in_path)
                    
                    status_text.text(f"Processing {display_path} ({i+1}/{len(files_to_process)})...")
                    
                    try:
                        audio = AudioSegment.from_file(in_path)
                        
                        if target_format == "mp3":
                            audio.export(out_path, format="mp3", bitrate=bitrate)
                        elif target_format == "flac":
                            audio.export(out_path, format="flac")
                        elif target_format == "mp4":
                            audio.export(out_path, format="mp4", bitrate=bitrate)
                        elif target_format == "wav":
                            audio.export(out_path, format="wav")
                            
                    except Exception as e:
                        st.error(f"Error with {display_path}: {e}")

                    progress_bar.progress((i + 1) / len(files_to_process))
                
                st.success(f"Done! Processed {len(files_to_process)} files.")
                st.write(f"Output saved to: `{output_root}`")

with tab2:
    st.subheader("Upload Files")
    uploaded_files = st.file_uploader(
        "Choose audio or video files", 
        type=UPLOAD_TYPES, 
        accept_multiple_files=True,
        help="Supports MP4, WAV, FLAC, MKV, etc."
    )
    if uploaded_files:
        if st.button("Convert Uploaded Files", type="primary"):
            output_dir = "converted_uploads"
            os.makedirs(output_dir, exist_ok=True)
            progress_bar = st.progress(0)
            
            for i, uploaded_file in enumerate(uploaded_files):
                base_name = os.path.splitext(uploaded_file.name)[0]
                ext = target_format
                output_path = os.path.join(output_dir, f"{base_name}.{ext}")
                
                try:
                    import tempfile
                    temp_ext = os.path.splitext(uploaded_file.name)[1]
                    
                    with tempfile.NamedTemporaryFile(delete=False, suffix=temp_ext) as tmp_file:
                        tmp_file.write(uploaded_file.read())
                        tmp_file_path = tmp_file.name
                    
                    audio = AudioSegment.from_file(tmp_file_path)
                    
                    if target_format == "mp3":
                        audio.export(output_path, format="mp3", bitrate=bitrate)
                    elif target_format == "flac":
                        audio.export(output_path, format="flac")
                    elif target_format == "mp4":
                        audio.export(output_path, format="mp4", bitrate=bitrate)
                    elif target_format == "wav":
                        audio.export(output_path, format="wav")
                    
                    os.remove(tmp_file_path)
                except Exception as e:
                    st.error(f"Failed to process {uploaded_file.name}: {e}")

                progress_bar.progress((i + 1) / len(uploaded_files))
            
            st.success("Conversion Complete!")
            st.markdown("### Download Files")
            output_files = sorted(os.listdir(output_dir))
            for f in output_files:
                file_path = os.path.join(output_dir, f)
                with open(file_path, "rb") as file:
                    mime = "audio/mpeg"
                    if f.endswith(".flac"): mime = "audio/flac"
                    elif f.endswith(".mp4"): mime = "video/mp4"
                    elif f.endswith(".wav"): mime = "audio/wav"
                    
                    st.download_button(label=f"Download {f}", data=file, file_name=f, mime=mime)