#!/bin/bash
# AMD Strix Halo (gfx1151) GPU metrics → node-exporter textfile (R13).
#
# AMD не выпустил рабочий Prometheus exporter для APU gfx1151:
# - amd/amd_smi_exporter — datacenter only (MI200/MI300)
# - ROCm/device-metrics-exporter — то же
# - amd-smi показывает N/A на Strix Halo (ROCm/ROCm#6035)
#
# Kernel exposes данные через sysfs/hwmon — этот скрипт парсит их и пишет
# в Prometheus textfile format. node-exporter с --collector.textfile.directory
# подхватит и отдаст /metrics endpoint.
#
# Запуск: cron / systemd timer каждые 15 секунд.
# Output: $TEXTFILE_DIR/amdgpu.prom
#
# См. deep-dive 03 §4 (Strix Halo GPU exporter — проблема индустрии).

set -euo pipefail

TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile}"
OUTPUT="${TEXTFILE_DIR}/amdgpu.prom"
TMP="${OUTPUT}.tmp"

mkdir -p "${TEXTFILE_DIR}"

# Helper: read sysfs value, return empty if missing
read_val() {
    local path="$1"
    [[ -r "${path}" ]] && cat "${path}" 2>/dev/null || true
}

{
    echo "# HELP amdgpu_temp_edge_celsius GPU edge temperature in Celsius"
    echo "# TYPE amdgpu_temp_edge_celsius gauge"
    echo "# HELP amdgpu_power_average_watts GPU power draw average"
    echo "# TYPE amdgpu_power_average_watts gauge"
    echo "# HELP amdgpu_sclk_hz GPU shader/core clock"
    echo "# TYPE amdgpu_sclk_hz gauge"
    echo "# HELP amdgpu_gpu_busy_percent GPU busy time in last sampling"
    echo "# TYPE amdgpu_gpu_busy_percent gauge"
    echo "# HELP amdgpu_vram_used_bytes VRAM used (small UMA FB on Strix Halo)"
    echo "# TYPE amdgpu_vram_used_bytes gauge"
    echo "# HELP amdgpu_vram_total_bytes VRAM total"
    echo "# TYPE amdgpu_vram_total_bytes gauge"
    echo "# HELP amdgpu_vis_vram_used_bytes Visible VRAM used (CPU-mappable subset)"
    echo "# TYPE amdgpu_vis_vram_used_bytes gauge"
    echo "# HELP amdgpu_gtt_used_bytes GTT used (unified memory pages — main storage on APU)"
    echo "# TYPE amdgpu_gtt_used_bytes gauge"
    echo "# HELP amdgpu_gtt_total_bytes GTT total"
    echo "# TYPE amdgpu_gtt_total_bytes gauge"

    for card_path in /sys/class/drm/card[0-9]*; do
        [[ -d "${card_path}/device" ]] || continue
        card=$(basename "${card_path}")
        device="${card_path}/device"

        # Skip non-AMD (vendor 0x1002 = AMD/ATI)
        vendor=$(read_val "${device}/vendor")
        [[ "${vendor}" == "0x1002" ]] || continue

        # Temperature: hwmon temp1_input в millidegrees C
        for hwmon in "${device}"/hwmon/hwmon*; do
            [[ -d "${hwmon}" ]] || continue
            temp_milli=$(read_val "${hwmon}/temp1_input")
            if [[ -n "${temp_milli}" ]]; then
                temp_c=$(LC_ALL=C awk "BEGIN { printf\"%.1f\", ${temp_milli} / 1000 }")
                echo "amdgpu_temp_edge_celsius{card=\"${card}\"} ${temp_c}"
            fi

            # Power: power1_average в microwatts
            power_uw=$(read_val "${hwmon}/power1_average")
            if [[ -n "${power_uw}" ]]; then
                power_w=$(LC_ALL=C awk "BEGIN { printf\"%.2f\", ${power_uw} / 1000000 }")
                echo "amdgpu_power_average_watts{card=\"${card}\"} ${power_w}"
            fi

            # Clocks: freq1 в Hz (kernel hwmon).
            # На Strix Halo (gfx1151) unified memory → нет отдельного freq2 (mclk).
            # Verified on real hardware 2026-05-19 — only freq1_input exists.
            sclk=$(read_val "${hwmon}/freq1_input")
            [[ -n "${sclk}" ]] && echo "amdgpu_sclk_hz{card=\"${card}\"} ${sclk}"
        done

        # GPU busy: gpu_busy_percent (0-100)
        busy=$(read_val "${device}/gpu_busy_percent")
        [[ -n "${busy}" ]] && echo "amdgpu_gpu_busy_percent{card=\"${card}\"} ${busy}"

        # VRAM used/total (bytes) — на Strix Halo это маленький UMA Frame Buffer (~512 MB).
        vram_used=$(read_val "${device}/mem_info_vram_used")
        [[ -n "${vram_used}" ]] && echo "amdgpu_vram_used_bytes{card=\"${card}\"} ${vram_used}"

        vram_total=$(read_val "${device}/mem_info_vram_total")
        [[ -n "${vram_total}" ]] && echo "amdgpu_vram_total_bytes{card=\"${card}\"} ${vram_total}"

        # Visible VRAM (CPU-mappable subset) — verified exists on gfx1151
        vis_vram_used=$(read_val "${device}/mem_info_vis_vram_used")
        [[ -n "${vis_vram_used}" ]] && echo "amdgpu_vis_vram_used_bytes{card=\"${card}\"} ${vis_vram_used}"

        # GTT used/total (unified memory pages для GPU — основной storage на Strix Halo)
        gtt_used=$(read_val "${device}/mem_info_gtt_used")
        [[ -n "${gtt_used}" ]] && echo "amdgpu_gtt_used_bytes{card=\"${card}\"} ${gtt_used}"

        gtt_total=$(read_val "${device}/mem_info_gtt_total")
        [[ -n "${gtt_total}" ]] && echo "amdgpu_gtt_total_bytes{card=\"${card}\"} ${gtt_total}"
    done
} > "${TMP}"

# Атомарная подмена (textfile collector видит файлы через inotify)
mv "${TMP}" "${OUTPUT}"
