#!/bin/bash

# Single-file media converter for images, videos, and PDFs.
#
# Usage:
#   media_convert.sh --input <file> --output <file> [options]
#
# The output filename determines the type:
#   *.webp (from image)  → converted WebP image
#   *.mp4  (from video)  → H.264 video + animated WebP + poster WebP
#   *.pdf  (from PDF)    → copy of original + poster PNG preview
#
# For videos, auxiliary files are written alongside the output:
#   <output_without_ext>_animated.webp
#   <output_without_ext>_poster.webp
#
# For PDFs, auxiliary file:
#   <output_without_ext>_poster.png
#
# Options:
#   --input <file>         Input file (required)
#   --output <file>        Output file (required)
#   --duration <seconds>   Animated WebP duration (default: 6)
#   --start <seconds>      Start time for video clip (default: 0)
#   --force                Overwrite existing output files
#   -h, --help             Show this help
#
# Exit codes:
#   0  success
#   1  usage / argument error
#   2  missing dependency
#   3  conversion failure

set -e

# ── Configuration ────────────────────────────────────────────────────
TARGET_HEIGHT=1080
VIDEO_BITRATE="6M"
AUDIO_BITRATE="192k"
VIDEO_CRF=15
IMAGE_QUALITY=95
WEBP_QUALITY=95
POSTER_QUALITY=95
WEBP_FPS=24

# ── Argument parsing ────────────────────────────────────────────────
INPUT_FILE=""
OUTPUT_FILE=""
DURATION=6
START_TIME=0
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --input)    INPUT_FILE="$2"; shift 2 ;;
        --output)   OUTPUT_FILE="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --start)    START_TIME="$2"; shift 2 ;;
        --force)    FORCE=1; shift ;;
        -h|--help)  sed -n '3,32p' "$0"; exit 0 ;;
        *)          echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$INPUT_FILE" ] || [ -z "$OUTPUT_FILE" ]; then
    echo "Usage: $0 --input <file> --output <file> [--duration N] [--start N] [--force]" >&2
    exit 1
fi

if [ ! -f "$INPUT_FILE" ]; then
    echo "Input file not found: $INPUT_FILE" >&2
    exit 1
fi

# ── Detect mode from file extension ─────────────────────────────────
get_ext() {
    local ext="${1##*.}"
    echo "$ext" | tr '[:upper:]' '[:lower:]'
}

INPUT_EXT=$(get_ext "$INPUT_FILE")
OUTPUT_EXT=$(get_ext "$OUTPUT_FILE")

MODE=""
case "$INPUT_EXT" in
    jpg|jpeg|png|gif|bmp|tiff|tif|webp) MODE="image" ;;
    mp4|mov|avi|mkv|webm|m4v)      MODE="video" ;;
    pdf)                            MODE="pdf" ;;
    *)
        echo "Unsupported input file type: .$INPUT_EXT" >&2
        exit 1
        ;;
esac

# Derive output prefix (output path without extension)
OUTPUT_PREFIX="${OUTPUT_FILE%.*}"
OUTPUT_DIR=$(dirname "$OUTPUT_FILE")
mkdir -p "$OUTPUT_DIR"

# ── Skip check (unless --force) ─────────────────────────────────────
should_skip() {
    local out="$1"
    [ "$FORCE" -eq 0 ] && [ -f "$out" ] && [ ! "$out" -ot "$INPUT_FILE" ]
}

# ── Dependency checks ───────────────────────────────────────────────
check_dependencies() {
    local missing=0

    if [ "$MODE" = "image" ]; then
        if ! command -v magick &>/dev/null; then
            echo "Error: ImageMagick (magick) is not installed." >&2
            missing=1
        fi
    fi

    if [ "$MODE" = "video" ]; then
        if ! command -v ffmpeg &>/dev/null; then
            echo "Error: ffmpeg is not installed." >&2
            missing=1
        fi
        if ! command -v ffprobe &>/dev/null; then
            echo "Error: ffprobe is not installed." >&2
            missing=1
        fi
        if command -v ffmpeg &>/dev/null; then
            if ! ffmpeg -encoders 2>/dev/null | grep -q "libwebp_anim"; then
                echo "Error: ffmpeg lacks libwebp_anim encoder support." >&2
                missing=1
            fi
        fi
    fi

    if [ "$MODE" = "pdf" ]; then
        if ! command -v gs &>/dev/null; then
            echo "Error: Ghostscript (gs) is not installed." >&2
            missing=1
        fi
    fi

    if [ $missing -eq 1 ]; then
        exit 2
    fi
}

check_dependencies

# ── Video helpers ───────────────────────────────────────────────────
has_video_stream() {
    local count
    count=$(ffprobe -v error -select_streams v -show_entries stream=codec_type \
            -of csv=p=0 "$1" 2>/dev/null | wc -l)
    [ "$count" -gt 0 ]
}

has_audio_stream() {
    local count
    count=$(ffprobe -v error -select_streams a -show_entries stream=codec_type \
            -of csv=p=0 "$1" 2>/dev/null | wc -l)
    [ "$count" -gt 0 ]
}

get_video_height() {
    ffprobe -v error -select_streams v:0 -show_entries stream=height \
        -of csv=p=0 "$1" 2>/dev/null | head -1 | tr -d ','
}

get_video_duration() {
    ffprobe -v error -show_entries format=duration \
        -of csv=p=0 "$1" 2>/dev/null | cut -d'.' -f1
}

get_video_bitrate() {
    ffprobe -v error -select_streams v:0 -show_entries stream=bit_rate \
        -of csv=p=0 "$1" 2>/dev/null | head -1 | tr -d ','
}

get_file_bitrate() {
    local duration
    duration=$(get_video_duration "$1")
    if [ -z "$duration" ] || [ "$duration" -eq 0 ]; then
        echo ""
        return
    fi
    local size
    size=$(stat -f%z "$1" 2>/dev/null || stat -c%s "$1" 2>/dev/null)
    echo $(( (size * 8) / duration ))
}

# Clamp start/duration to source duration
clamp_video_times() {
    local source_duration
    source_duration=$(get_video_duration "$INPUT_FILE")

    local src_dur_int=${source_duration%%.*}
    local start_int=${START_TIME%%.*}
    local dur_int=${DURATION%%.*}

    if [ -n "$src_dur_int" ] && [ "$src_dur_int" -gt 0 ]; then
        if [ "$start_int" -ge "$src_dur_int" ]; then
            CLAMPED_START=0
        else
            CLAMPED_START=$START_TIME
        fi
        local remaining=$((src_dur_int - ${CLAMPED_START%%.*}))
        if [ "$dur_int" -gt "$remaining" ]; then
            CLAMPED_DURATION=$remaining
        else
            CLAMPED_DURATION=$DURATION
        fi
    else
        CLAMPED_START=$START_TIME
        CLAMPED_DURATION=$DURATION
    fi
}

# ── Image conversion ────────────────────────────────────────────────
convert_image() {
    local out="$OUTPUT_FILE"

    if should_skip "$out"; then
        echo "Skipping (exists): $out"
        return 0
    fi

    if magick "$INPUT_FILE" \
        -auto-orient \
        -resize "${TARGET_HEIGHT}x${TARGET_HEIGHT}>" \
        -strip \
        -quality "$IMAGE_QUALITY" \
        "$out" 2>&1; then
        touch -r "$INPUT_FILE" "$out" 2>/dev/null || true
        echo "Created: $out"
    else
        echo "Image conversion failed" >&2
        exit 3
    fi
}

# ── Video conversion ────────────────────────────────────────────────
convert_video_mp4() {
    local out="$OUTPUT_FILE"

    if should_skip "$out"; then
        echo "Skipping (exists): $out"
        return 0
    fi

    local video_height
    video_height=$(get_video_height "$INPUT_FILE")

    local scale_filter=""
    local actual_height=${video_height:-$TARGET_HEIGHT}

    if [ -z "$video_height" ] || [ "$video_height" -eq 0 ]; then
        scale_filter="-vf scale=-2:${TARGET_HEIGHT}"
        actual_height=$TARGET_HEIGHT
    elif [ "$video_height" -gt "$TARGET_HEIGHT" ]; then
        scale_filter="-vf scale=-2:${TARGET_HEIGHT}"
        actual_height=$TARGET_HEIGHT
    else
        scale_filter="-vf scale=trunc(iw/2)*2:trunc(ih/2)*2"
    fi

    # Adaptive bitrate based on resolution
    local target_bitrate_num=6000000
    if [ "$actual_height" -lt 480 ]; then
        target_bitrate_num=2000000
    elif [ "$actual_height" -lt 720 ]; then
        target_bitrate_num=3000000
    elif [ "$actual_height" -lt 1080 ]; then
        target_bitrate_num=4000000
    fi

    # Cap at source bitrate
    local source_bitrate
    source_bitrate=$(get_video_bitrate "$INPUT_FILE")
    if [ -z "$source_bitrate" ] || [ "$source_bitrate" = "N/A" ]; then
        source_bitrate=$(get_file_bitrate "$INPUT_FILE")
    fi
    if [ -n "$source_bitrate" ] && [ "$source_bitrate" -gt 0 ] 2>/dev/null; then
        if [ "$source_bitrate" -lt "$target_bitrate_num" ]; then
            target_bitrate_num=$source_bitrate
        fi
    fi

    local target_bitrate
    if [ "$target_bitrate_num" -ge 1000000 ]; then
        target_bitrate="$((target_bitrate_num / 1000000))M"
    else
        target_bitrate="${target_bitrate_num}"
    fi

    local audio_opts=""
    if has_audio_stream "$INPUT_FILE"; then
        audio_opts="-c:a aac -b:a $AUDIO_BITRATE"
    else
        audio_opts="-an"
    fi

    local bufsize_num=$((target_bitrate_num * 2))
    local bufsize
    if [ "$bufsize_num" -ge 1000000 ]; then
        bufsize="$((bufsize_num / 1000000))M"
    else
        bufsize="${bufsize_num}"
    fi

    ffmpeg -y -nostdin -i "$INPUT_FILE" \
        -c:v libx264 -profile:v baseline -level 3.0 \
        -preset medium -crf $VIDEO_CRF \
        -maxrate "$target_bitrate" -bufsize "$bufsize" \
        $scale_filter \
        $audio_opts \
        -movflags +faststart \
        -pix_fmt yuv420p \
        "$out" \
        -loglevel error 2>&1

    echo "Created: $out"
}

convert_video_animated_webp() {
    local out="${OUTPUT_PREFIX}_animated.webp"

    if should_skip "$out"; then
        echo "Skipping (exists): $out"
        return 0
    fi

    ffmpeg -y -nostdin -ss "$CLAMPED_START" -i "$INPUT_FILE" \
        -t "$CLAMPED_DURATION" \
        -vf "scale=-2:${TARGET_HEIGHT},fps=${WEBP_FPS}" \
        -c:v libwebp_anim \
        -loop 0 \
        -quality $WEBP_QUALITY \
        -an \
        "$out" \
        -loglevel error 2>&1

    echo "Created: $out"
}

convert_video_poster() {
    local out="${OUTPUT_PREFIX}_poster.webp"

    if should_skip "$out"; then
        echo "Skipping (exists): $out"
        return 0
    fi

    ffmpeg -y -nostdin -ss "$CLAMPED_START" -i "$INPUT_FILE" \
        -vframes 1 \
        -vf "scale=-2:${TARGET_HEIGHT}" \
        -quality $POSTER_QUALITY \
        "$out" \
        -loglevel error 2>&1

    echo "Created: $out"
}

convert_video() {
    if ! has_video_stream "$INPUT_FILE"; then
        echo "No video stream found in: $INPUT_FILE" >&2
        exit 3
    fi

    clamp_video_times
    convert_video_mp4
    convert_video_animated_webp
    convert_video_poster
}

# ── PDF conversion ──────────────────────────────────────────────────
convert_pdf() {
    local out="$OUTPUT_FILE"
    local poster="${OUTPUT_PREFIX}_poster.png"

    local need_copy=1
    local need_poster=1

    if should_skip "$out"; then
        need_copy=0
    fi
    if should_skip "$poster"; then
        need_poster=0
    fi

    if [ $need_copy -eq 0 ] && [ $need_poster -eq 0 ]; then
        echo "Skipping (exists): $out"
        return 0
    fi

    if [ $need_copy -eq 1 ]; then
        if cp "$INPUT_FILE" "$out"; then
            touch -r "$INPUT_FILE" "$out" 2>/dev/null || true
            echo "Created: $out"
        else
            echo "PDF copy failed" >&2
            exit 3
        fi
    fi

    if [ $need_poster -eq 1 ]; then
        if gs -dSAFER -dBATCH -dNOPAUSE -dQUIET \
            -dFirstPage=1 -dLastPage=1 \
            -sDEVICE=png16m -r150 \
            -sOutputFile="$poster" \
            "$INPUT_FILE" 2>/dev/null; then
            touch -r "$INPUT_FILE" "$poster" 2>/dev/null || true
            echo "Created: $poster"
        else
            echo "PDF preview generation failed" >&2
            exit 3
        fi
    fi
}

# ── Main ────────────────────────────────────────────────────────────
case "$MODE" in
    image) convert_image ;;
    video) convert_video ;;
    pdf)   convert_pdf ;;
esac
