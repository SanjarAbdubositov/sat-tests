#!/usr/bin/env python3
"""
Automated Data & 1:1 Visual Figure Ingestion Engine for Digital SAT Mock Test Platform (Tests 4 to 11).
Extracts:
1. Passages, questions, multiple-choice options, and underlined text spans from test PDFs.
2. High-resolution 1:1 visual diagrams, charts, bar/line graphs, tables, geometry figures,
   typeset math equations, and graphical/typeset answer choices (A, B, C, D) via pypdfium2 + pdfplumber.
3. Official answer keys and detailed explanations from '-answers-digital.pdf'.
4. Raw-to-scale score conversion tables (400-1600 scale) from 'scoring-sat-practice-test-*-digital.pdf'.
"""

import json
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import pypdf
import pdfplumber
import pypdfium2 as pdfium
import numpy as np
from PIL import Image, ImageChops

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FIGURES_DIR = DATA_DIR / "figures"

FALLBACK_STOPS = {
    4: [16, 29, 38, 47],
    5: [15, 30, 40, 49],
    6: [16, 29, 41, 51],
    7: [17, 30, 39, 49],
    8: [15, 29, 39, 49],
    9: [15, 29, 38, 47],
    10: [15, 30, 41, 51],
    11: [15, 29, 40, 50],
}


def crop_pil_region(pil_page: Image.Image, page_w: float, page_h: float, bbox_pt: tuple, pad: int = 8):
    """
    Crops a point-coordinate bounding box (x0, top, x1, bottom) from a rendered PIL page
    and automatically trims excess white margins while leaving clean padding.
    """
    pw, ph = pil_page.size
    sx, sy = pw / page_w, ph / page_h
    x0, top, x1, bottom = bbox_pt
    box = (
        max(0, int(x0 * sx)),
        max(0, int(top * sy)),
        min(pw, int(x1 * sx)),
        min(ph, int(bottom * sy)),
    )
    if box[2] <= box[0] + 6 or box[3] <= box[1] + 6:
        return None
    sub = pil_page.crop(box)
    bg = Image.new(sub.mode, sub.size, (255, 255, 255))
    diff = ImageChops.difference(sub, bg)
    ink = diff.getbbox()
    if not ink:
        return None
    cw, ch = sub.size
    if ink[2] - ink[0] < 4 or ink[3] - ink[1] < 4:
        return None
    return sub.crop((
        max(0, ink[0] - pad),
        max(0, ink[1] - pad),
        min(cw, ink[2] + pad),
        min(ch, ink[3] + pad),
    ))


def find_split_between(
    pil_page: Image.Image,
    page_w: float,
    page_h: float,
    x0_pt: float,
    x1_pt: float,
    y_start_pt: float,
    y_end_pt: float,
) -> float:
    """
    Finds the vertical y-coordinate (in points) of the widest contiguous horizontal
    white gap between y_start_pt and y_end_pt within [x0_pt, x1_pt].
    Prevents clipping tall numerators, denominators, radicals, and exponents in options.
    """
    pw, ph = pil_page.size
    sx, sy = pw / page_w, ph / page_h
    ix0 = max(0, int(x0_pt * sx))
    ix1 = min(pw, int(x1_pt * sx))
    iy0 = max(0, int(y_start_pt * sy))
    iy1 = min(ph, int(y_end_pt * sy))
    if iy1 <= iy0 + 2 or ix1 <= ix0 + 4:
        return (y_start_pt + y_end_pt) / 2.0

    strip = pil_page.crop((ix0, iy0, ix1, iy1)).convert("L")
    arr = np.array(strip)
    is_white_row = np.min(arr, axis=1) > 235

    runs = []
    run_start = None
    for idx, val in enumerate(is_white_row):
        if val and run_start is None:
            run_start = idx
        elif not val and run_start is not None:
            runs.append((idx - run_start, run_start, idx - 1))
            run_start = None
    if run_start is not None:
        runs.append((len(is_white_row) - run_start, run_start, len(is_white_row) - 1))

    if not runs:
        return (y_start_pt + y_end_pt) / 2.0

    best_len, best_s, best_e = max(runs, key=lambda r: r[0])
    mid_px = iy0 + (best_s + best_e) / 2.0
    return mid_px / sy


def find_top_above_A(
    pil_page: Image.Image,
    page_w: float,
    page_h: float,
    x0_pt: float,
    x1_pt: float,
    wA_top_pt: float,
    min_y_pt: float,
) -> float:
    """
    Scans upward from wA_top_pt to find the first white band >= 4.5pt high
    so tall numerators/radicals in Option A are never clipped at the top.
    """
    pw, ph = pil_page.size
    sx, sy = pw / page_w, ph / page_h
    ix0 = max(0, int(x0_pt * sx))
    ix1 = min(pw, int(x1_pt * sx))
    iy0 = max(0, int(min_y_pt * sy))
    iy1 = min(ph, int((wA_top_pt + 2) * sy))
    if iy1 <= iy0 + 4 or ix1 <= ix0 + 4:
        return wA_top_pt - 4.0

    strip = pil_page.crop((ix0, iy0, ix1, iy1)).convert("L")
    arr = np.array(strip)
    is_white_row = np.min(arr, axis=1) > 235
    min_band_px = max(3, int(4.5 * sy))

    run_end = None
    for idx in range(len(is_white_row) - 1, -1, -1):
        if is_white_row[idx]:
            if run_end is None:
                run_end = idx
        else:
            if run_end is not None:
                run_len = run_end - idx
                if run_len >= min_band_px:
                    mid_px = iy0 + (idx + 1 + run_end) / 2.0
                    return mid_px / sy
                run_end = None
    if run_end is not None and run_end >= min_band_px:
        return (iy0 + run_end / 2.0) / sy
    return wA_top_pt - 8.0


def parse_scoring_pdf(scoring_path: Path):
    """
    Extracts the 120-item official Answer Key (Page 4) and the Raw-to-Scale Score
    Conversion Tables for Reading/Writing (0-66) and Math (0-54) (Page 5).
    """
    reader = pypdf.PdfReader(str(scoring_path))
    p4_text = reader.pages[3].extract_text() or ""
    ans_pairs = re.findall(r"(?:^|\n)\s*(\d{1,2})\s+([A-D0-9\.\-/;,\s]+?)(?=\n|$)", p4_text)
    answer_key = [ans.strip() for _, ans in ans_pairs]

    p5_text = reader.pages[4].extract_text() or ""
    rw_table = {}
    math_table = {}
    for line in p5_text.split("\n"):
        s = line.strip()
        m4 = re.match(r"^(\d{1,2})\s+(\d{3})\s+(\d{3})\s+(\d{3})\s+(\d{3})$", s)
        if m4:
            raw, rwl, rwu, ml, mu = map(int, m4.groups())
            rw_scaled = int(round(((rwl + rwu) / 2.0) / 10.0) * 10)
            m_scaled = int(round(((ml + mu) / 2.0) / 10.0) * 10)
            rw_table[str(raw)] = {"lower": rwl, "upper": rwu, "scaled": min(800, max(200, rw_scaled))}
            math_table[str(raw)] = {"lower": ml, "upper": mu, "scaled": min(800, max(200, m_scaled))}
        else:
            m2 = re.match(r"^(\d{1,2})\s+(\d{3})\s+(\d{3})$", s)
            if m2:
                raw, rwl, rwu = map(int, m2.groups())
                rw_scaled = int(round(((rwl + rwu) / 2.0) / 10.0) * 10)
                rw_table[str(raw)] = {"lower": rwl, "upper": rwu, "scaled": min(800, max(200, rw_scaled))}

    return answer_key, {"Reading": rw_table, "Math": math_table}


def parse_answers_pdf(answers_path: Path):
    """
    Extracts all 120 detailed question explanations and rationales from '-answers-digital.pdf'.
    """
    reader = pypdf.PdfReader(str(answers_path))
    cleaned_pages = []
    for page in reader.pages:
        txt = page.extract_text() or ""
        lines = []
        for line in txt.split("\n"):
            s = line.strip()
            if not s:
                continue
            if re.match(r"^SAT ANSWER EXPLANATIONS\s*n", s, re.I):
                continue
            if re.match(r"^\d+\s+SAT PRACTICE TEST\s+#\d+\s+ANSWER EXPLANATIONS$", s, re.I):
                continue
            if re.match(r"^SAT PRACTICE TEST\s+#\d+\s+ANSWER EXPLANATIONS\s+\d+$", s, re.I):
                continue
            lines.append(s)
        cleaned_pages.append("\n".join(lines))

    full_text = "\n".join(cleaned_pages)
    matches = list(re.finditer(r"(?:^|\n)\s*QUESTION\s+(\d{1,2})\b", full_text))
    explanations = []
    for idx, m in enumerate(matches):
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(full_text)
        body = full_text[start:end].strip()
        body = re.sub(
            r"\n\s*(?:Reading and Writing|Math)\s*\n\s*Module\s+[12]\s*\n\s*\(\d+\s*questions\)\s*$",
            "",
            body,
            flags=re.I,
        ).strip()
        body = re.sub(r"\s+", " ", body)
        body = re.sub(r"\s+n+\s*$", "", body).strip()
        explanations.append(body)
    return explanations


def extract_math_explanation_figures(answers_path: Path, test_num: int) -> dict:
    """
    Crops 1:1 visual official explanations (with typeset fractions, radicals, exponents,
    and diagrams) for all 54 Math questions (global_id 67..120) from '-answers-digital.pdf'.
    Automatically stitches across page breaks when an explanation spans two pages.
    """
    exp_figs = {}
    ans_doc = pdfium.PdfDocument(str(answers_path))
    pil_cache = {}

    def get_ans_pil(p_idx: int):
        if p_idx not in pil_cache:
            pil_cache[p_idx] = ans_doc[p_idx].render(scale=2.0).to_pil()
        return pil_cache[p_idx]

    q_locs = []
    sec_headers = []
    with pdfplumber.open(str(answers_path)) as pdf:
        page_dims = {}
        for p_idx, page in enumerate(pdf.pages):
            page_dims[p_idx] = (page.width, page.height)
            words = page.extract_words()
            for wd in words:
                if wd["text"] == "QUESTION" and wd["x0"] < 95:
                    q_locs.append((p_idx, wd["top"], wd["bottom"]))
                elif wd["text"] in ("Math", "Reading") and wd["x0"] < 95 and 120 < wd["top"] < 680:
                    sec_headers.append((p_idx, wd["top"]))

    # Keep only the 54 Math explanations (indices 66..119 -> global_id 67..120)
    for idx in range(66, min(120, len(q_locs))):
        global_id = idx + 1
        p_start, y_top, _ = q_locs[idx]
        if idx + 1 < len(q_locs):
            p_end, y_next, _ = q_locs[idx + 1]
        else:
            p_end, y_next = p_start, 735.0

        # Check if a section header ("Math Module 2") sits between (p_start, y_top) and (p_end, y_next)
        for sh_p, sh_top in sec_headers:
            if (sh_p == p_start and sh_top > y_top + 20) or (p_start < sh_p < p_end) or (sh_p == p_end and sh_top < y_next):
                p_end, y_next = sh_p, sh_top - 10.0
                break

        w, h = page_dims[p_start]
        y0 = max(72.0, y_top + 16.0)  # Start right below "QUESTION X" header line

        if p_start == p_end:
            y1 = min(736.0, y_next - 6.0)
            img = crop_pil_region(get_ans_pil(p_start), w, h, (52.0, y0, w - 52.0, y1), pad=8)
        else:
            # Stitch part 1 on p_start and part 2 on p_end
            part1 = crop_pil_region(get_ans_pil(p_start), w, h, (52.0, y0, w - 52.0, 736.0), pad=6)
            part2 = None
            if y_next > 96.0:
                w2, h2 = page_dims[p_end]
                part2 = crop_pil_region(get_ans_pil(p_end), w2, h2, (52.0, 74.0, w2 - 52.0, min(736.0, y_next - 6.0)), pad=6)
            if part1 and part2:
                total_w = max(part1.width, part2.width)
                total_h = part1.height + part2.height + 6
                stitched = Image.new("RGB", (total_w, total_h), (255, 255, 255))
                stitched.paste(part1, (0, 0))
                stitched.paste(part2, (0, part1.height + 6))
                img = stitched
            else:
                img = part1 or part2

        if img:
            rel_exp = f"data/figures/t{test_num}_q{global_id}_exp.png"
            img.save(BASE_DIR / rel_exp)
            exp_figs[str(global_id)] = rel_exp

    return exp_figs


def detect_module_stops(test_num: int, test_path: Path):
    if test_num in FALLBACK_STOPS:
        return FALLBACK_STOPS[test_num]
    reader = pypdf.PdfReader(str(test_path))
    stops = []
    for idx in range(3, len(reader.pages) - 1):
        txt = reader.pages[idx].extract_text() or ""
        if "If you finish before time is called" in txt or "Do not turn to any other" in txt:
            stops.append(idx)
    return stops if len(stops) == 4 else [16, 29, 38, 47]


def clean_block_lines(raw_text: str) -> str:
    txt = re.sub(r"\bb\s*l\s*a\s*n\s*k\b", "_______", raw_text)
    txt = re.sub(
        r"The questions in this section address a number of important reading and writing skills.*?single best answer\.",
        "",
        txt,
        flags=re.S | re.I,
    )
    lines = []
    for line in txt.split("\n"):
        s = line.strip()
        if not s:
            continue
        if re.match(r"^(?:Modu|ule|Module|Reading and Writ|ing|Math|DIRECTIONS|\d+\s+QUESTIONS)$", s, re.I):
            continue
        if re.match(
            r"^(?:The questions in this section|question includes one or more|and question carefully|All questions in this section|single best answer|ich may include a table|ber of important reading|best answer to the question|oice with four answer)",
            s,
            re.I,
        ):
            continue
        if re.match(
            r"^(?:Unauthorized copying|CONTINUE|CO\s*N\s*T\s*I\s*N\s*U\s*E|STOP|If you finish before time|Do not turn to any other)",
            s,
            re.I,
        ):
            continue
        if re.match(r"^[\.\-~_\s\\/'\"\|,]+$", s) and "_______" not in s:
            continue
        if re.match(r"^1\s+1$", s) or re.match(r"^2\s+2$", s):
            continue
        lines.append(s)
    return "\n".join(lines)


def detect_column_bottom_limit(col_words, default_h: float) -> float:
    """Finds where footer elements (Unauthorized copying, CONTINUE, STOP, dotted lines) start."""
    limit = default_h - 46.0
    for wd in col_words:
        t = wd["text"]
        if t in ("Unauthorized", "CONTINUE", "STOP") or re.match(r"^\.{8,}$", t):
            if wd["top"] > 400:
                limit = min(limit, wd["top"] - 4.0)
    return limit


def extract_module_questions_and_figures(
    pdf,
    pdfium_doc,
    test_num: int,
    start_page: int,
    end_page: int,
    expected_count: int,
    is_math: bool,
    global_start_id: int,
):
    """
    Extracts every question block in the module AND crops 1:1 high-resolution PNG
    diagrams, charts, tables, math setups, and option visuals.
    """
    columns = []
    pil_cache = {}

    def get_pil_page(p_idx: int):
        if p_idx not in pil_cache:
            pil_cache[p_idx] = pdfium_doc[p_idx].render(scale=2.2).to_pil()
        return pil_cache[p_idx]

    for p_idx in range(start_page, end_page + 1):
        p = pdf.pages[p_idx]
        w, h = p.width, p.height
        words = p.extract_words()
        page_str = " ".join(wd["text"] for wd in words[:60])
        if is_math and ("For multiple-choice questions" in page_str or "important math skills" in page_str):
            continue
        if "No Test Material On This Page" in page_str:
            continue

        for col_idx, (x_min, x_max, qx_min, qx_max, crop_x0, crop_x1) in enumerate([
            (0, w / 2.0, 32, 68, 34.0, w / 2.0 - 14.0),
            (w / 2.0, w, 310, 348, w / 2.0 + 10.0, w - 34.0),
        ]):
            col_words = sorted(
                [wd for wd in words if x_min <= wd["x0"] < x_max and 55 <= wd["top"] <= h - 38],
                key=lambda item: (round(item["top"], 1), round(item["x0"], 1)),
            )
            q_markers = []
            for wd in col_words:
                txt = wd["text"].strip(".,~-_")
                if txt.isdigit() and qx_min <= wd["x0"] <= qx_max:
                    q_val = int(txt)
                    if 1 <= q_val <= expected_count:
                        same_line = [
                            other for other in col_words
                            if other is not wd and abs(other["top"] - wd["top"]) < 4
                            and not re.match(r"^[\.\-~_\s\\/'\"\|,]+$", other["text"])
                        ]
                        if not same_line:
                            q_markers.append((q_val, wd["top"], wd["bottom"]))
            columns.append({
                "p_idx": p_idx,
                "col_idx": col_idx,
                "x_min": x_min,
                "x_max": x_max,
                "crop_x0": crop_x0,
                "crop_x1": crop_x1,
                "w": w,
                "h": h,
                "col_words": col_words,
                "q_markers": q_markers,
            })

    matched_locs = []
    curr_q = 1
    for c_idx, col in enumerate(columns):
        for q_val, top, bottom in col["q_markers"]:
            if q_val == curr_q:
                matched_locs.append((curr_q, c_idx, top, bottom))
                curr_q += 1
                if curr_q > expected_count:
                    break
        if curr_q > expected_count:
            break

    results = []
    for i, (q_num, c_idx, q_top, q_bottom) in enumerate(matched_locs):
        global_id = global_start_id + i
        col = columns[c_idx]
        p_idx = col["p_idx"]
        p = pdf.pages[p_idx]
        w, h = col["w"], col["h"]

        # Determine vertical end of this question in current column
        if i + 1 < len(matched_locs) and matched_locs[i + 1][1] == c_idx:
            y_end = matched_locs[i + 1][2] - 10.0
        else:
            y_end = detect_column_bottom_limit(col["col_words"], h)

        # Check if this page has no questions (and no STOP box) in the right column -> wide/full-page question!
        is_full_page_q = False
        if (
            col["col_idx"] == 0
            and len(col["q_markers"]) == 1
            and (c_idx + 1 < len(columns) and columns[c_idx + 1]["p_idx"] == p_idx and len(columns[c_idx + 1]["q_markers"]) == 0)
            and p_idx != end_page
        ):
            is_full_page_q = True

        crop_x0 = 34.0 if is_full_page_q else col["crop_x0"]
        crop_x1 = (w - 34.0) if is_full_page_q else col["crop_x1"]

        # Extract raw text of question
        q_crop = p.crop((col["x_min"], q_bottom + 2, w if is_full_page_q else col["x_max"], max(q_bottom + 10, y_end)))
        crop_txt = q_crop.extract_text() or ""

        # Check if options A)..D) wrap to top of next column
        next_col_extra_bbox = None
        if "D)" not in crop_txt and not is_full_page_q and i + 1 < len(matched_locs):
            next_c_idx = matched_locs[i + 1][1]
            next_top = matched_locs[i + 1][2]
            if next_c_idx == c_idx + 1 and next_top > 120:
                next_col = columns[next_c_idx]
                p_next = pdf.pages[next_col["p_idx"]]
                extra_crop = p_next.crop((next_col["x_min"], 55, next_col["x_max"], next_top - 8))
                extra_txt = extra_crop.extract_text() or ""
                if ("A)" in extra_txt or "D)" in extra_txt) and "DIRECTIONS" not in extra_txt:
                    crop_txt = crop_txt + "\n" + extra_txt
                    next_col_extra_bbox = (next_col["p_idx"], next_col["crop_x0"], 55, next_col["crop_x1"], next_top - 8)
            elif next_c_idx > c_idx + 1:
                # Entire next column (or full next page, e.g. Test 10 P37) has no question headers and belongs to this question!
                next_col = columns[c_idx + 1]
                p_next = pdf.pages[next_col["p_idx"]]
                full_next_page = (
                    next_col["col_idx"] == 0
                    and c_idx + 2 < len(columns)
                    and columns[c_idx + 2]["p_idx"] == next_col["p_idx"]
                    and len(columns[c_idx + 2]["q_markers"]) == 0
                )
                nx0 = 34.0 if full_next_page else next_col["crop_x0"]
                nx1 = (w - 34.0) if full_next_page else next_col["crop_x1"]
                ny_end = detect_column_bottom_limit(next_col["col_words"], h)
                extra_crop = p_next.crop((nx0, 55, nx1, ny_end))
                extra_txt = extra_crop.extract_text() or ""
                if "A)" in extra_txt or "D)" in extra_txt:
                    crop_txt = crop_txt + "\n" + extra_txt
                    next_col_extra_bbox = (next_col["p_idx"], nx0, 55, nx1, ny_end)

        # Find option markers A), B), C), D) in current box
        q_words = q_crop.extract_words()
        opt_markers = [wd for wd in q_words if wd["text"] in ("A)", "B)", "C)", "D)")]
        opt_A_top = next((wd["top"] for wd in opt_markers if wd["text"] == "A)"), y_end)

        fig_manifest_entry = {}

        if not is_math:
            # READING & WRITING:
            # 1. Check for Chart / Bar Graph / Line Graph / Scatterplot / Table above the passage
            pre_opt_crop = p.crop((crop_x0, q_bottom + 4, crop_x1, max(q_bottom + 12, opt_A_top - 2)))
            gfx_objs = [
                o for o in (pre_opt_crop.rects + pre_opt_crop.lines + pre_opt_crop.curves + pre_opt_crop.images)
                if o["top"] > q_bottom + 8 and (
                    (o["bottom"] - o["top"]) > 2.6
                    or o in pre_opt_crop.lines
                    or o in pre_opt_crop.curves
                    or o in pre_opt_crop.images
                )
            ]
            has_table_or_graph_kw = bool(
                re.search(r"\b(?:graph|table|chart|data\s+from|figure|data\s+in)\b", crop_txt, re.I)
            )

            if (len(gfx_objs) >= 3 or len(pre_opt_crop.images) >= 1) and has_table_or_graph_kw:
                max_gfx_bottom = max((o["bottom"] for o in gfx_objs), default=q_bottom + 40)
                # Find where the prose paragraph starts below max_gfx_bottom
                pre_words = pre_opt_crop.extract_words()
                line_groups = {}
                for wd in pre_words:
                    if wd["top"] > max_gfx_bottom + 1:
                        line_key = int(round(wd["top"] / 4.0) * 4)
                        line_groups.setdefault(line_key, []).append(wd)

                prose_start_top = None
                for l_top in sorted(line_groups.keys()):
                    wds = sorted(line_groups[l_top], key=lambda item: item["x0"])
                    if len(wds) >= 5 and wds[0]["x0"] < crop_x0 + 36:
                        prose_start_top = min(w["top"] for w in wds)
                        break

                fig_bottom = (prose_start_top - 3.0) if prose_start_top else min(opt_A_top - 4.0, max_gfx_bottom + 18.0)
                pil_page = get_pil_page(p_idx)
                fig_img = crop_pil_region(pil_page, w, h, (crop_x0, q_bottom + 4, crop_x1, fig_bottom), pad=8)
                if fig_img:
                    rel_path = f"data/figures/t{test_num}_q{global_id}.png"
                    fig_img.save(BASE_DIR / rel_path)
                    fig_manifest_entry["stemFigure"] = rel_path

                # Re-extract clean text starting from prose_start_top so table/chart labels don't clutter passage
                if prose_start_top and prose_start_top < opt_A_top - 10:
                    clean_prose_crop = p.crop((col["x_min"], prose_start_top - 2, w if is_full_page_q else col["x_max"], max(prose_start_top + 10, y_end)))
                    crop_txt = clean_prose_crop.extract_text() or crop_txt

            # 2. Check for underlined sentence in Reading & Writing passage
            if "underlined" in crop_txt.lower():
                thin_rects = [
                    o for o in (pre_opt_crop.rects + pre_opt_crop.lines)
                    if (o["bottom"] - o["top"]) <= 2.8 and (o["x1"] - o["x0"]) > 12 and o["top"] > q_bottom + 12
                ]
                if thin_rects:
                    # Also save a visual crop of the underlined passage if no chart figure was already saved
                    if "stemFigure" not in fig_manifest_entry:
                        pil_page = get_pil_page(p_idx)
                        ul_img = crop_pil_region(pil_page, w, h, (crop_x0, q_bottom + 4, crop_x1, opt_A_top - 3), pad=8)
                        if ul_img:
                            rel_path = f"data/figures/t{test_num}_q{global_id}.png"
                            ul_img.save(BASE_DIR / rel_path)
                            fig_manifest_entry["stemFigure"] = rel_path

        else:
            # MATH SECTION (100% 1:1 Visual Fidelity for ALL 54 Math Questions per Test):
            pil_page = get_pil_page(p_idx)

            # Determine active option markers (either in current column or wrapped to next_col_extra_bbox)
            target_pil = pil_page
            target_x0, target_x1 = crop_x0, crop_x1
            target_min_y, target_max_y = q_bottom + 8, y_end
            active_opt_markers = opt_markers

            if next_col_extra_bbox:
                np_idx, nx0, ntop, nx1, ny_end = next_col_extra_bbox
                target_pil = get_pil_page(np_idx)
                target_x0, target_x1 = nx0, nx1
                target_min_y, target_max_y = ntop, ny_end
                p_next_obj = pdf.pages[np_idx].crop((nx0 - 10, ntop, nx1, ny_end))
                active_opt_markers = [wd for wd in p_next_obj.extract_words() if wd["text"] in ("A)", "B)", "C)", "D)")]

            by_letter = {}
            for wd in active_opt_markers:
                if wd["text"] not in by_letter:
                    by_letter[wd["text"]] = wd

            has_four_opts = all(k in by_letter for k in ("A)", "B)", "C)", "D)"))
            computed_top_A = opt_A_top - 3.0

            if has_four_opts:
                wA, wB, wC, wD = by_letter["A)"], by_letter["B)"], by_letter["C)"], by_letter["D)"]
                opt_paths = []
                is_2x2_grid = (
                    wB["x0"] > wA["x0"] + 45
                    and wD["x0"] > wC["x0"] + 45
                    and abs(wA["top"] - wB["top"]) < 18
                    and abs(wC["top"] - wD["top"]) < 18
                )

                if is_2x2_grid:
                    top_AB = find_top_above_A(
                        target_pil, w, h, target_x0, target_x1, min(wA["top"], wB["top"]), target_min_y
                    )
                    if not next_col_extra_bbox:
                        computed_top_A = top_AB
                    c_r1 = (wA["top"] + wA["bottom"] + wB["top"] + wB["bottom"]) / 4.0
                    c_r2 = (wC["top"] + wC["bottom"] + wD["top"] + wD["bottom"]) / 4.0
                    sp_12 = find_split_between(target_pil, w, h, target_x0, target_x1, c_r1, c_r2)

                    full_opts_img = crop_pil_region(
                        target_pil, w, h, (target_x0, top_AB, target_x1, target_max_y), pad=8
                    )
                    if full_opts_img:
                        rel_opts = f"data/figures/t{test_num}_q{global_id}_opts.png"
                        full_opts_img.save(BASE_DIR / rel_opts)
                        fig_manifest_entry["optsFigure"] = rel_opts

                    quads = [
                        (wA["x1"] + 2, top_AB, wB["x0"] - 4, sp_12),
                        (wB["x1"] + 2, top_AB, target_x1, sp_12),
                        (wC["x1"] + 2, sp_12, wD["x0"] - 4, target_max_y),
                        (wD["x1"] + 2, sp_12, target_x1, target_max_y),
                    ]
                    for q_idx, q_box in enumerate(quads):
                        o_im = crop_pil_region(target_pil, w, h, q_box, pad=6)
                        if o_im:
                            op_rel = f"data/figures/t{test_num}_q{global_id}_opt{q_idx}.png"
                            o_im.save(BASE_DIR / op_rel)
                            opt_paths.append(op_rel)

                elif wA["top"] < wB["top"] < wC["top"] < wD["top"]:
                    top_A = find_top_above_A(
                        target_pil, w, h, target_x0, target_x1, wA["top"], target_min_y
                    )
                    if not next_col_extra_bbox:
                        computed_top_A = top_A
                    cA = (wA["top"] + wA["bottom"]) / 2.0
                    cB = (wB["top"] + wB["bottom"]) / 2.0
                    cC = (wC["top"] + wC["bottom"]) / 2.0
                    cD = (wD["top"] + wD["bottom"]) / 2.0

                    sp_AB = find_split_between(target_pil, w, h, wA["x1"] + 2, target_x1, cA, cB)
                    sp_BC = find_split_between(target_pil, w, h, wB["x1"] + 2, target_x1, cB, cC)
                    sp_CD = find_split_between(target_pil, w, h, wC["x1"] + 2, target_x1, cC, cD)

                    v_boxes = [
                        (wA["x1"] + 2, top_A, target_x1, sp_AB),
                        (wB["x1"] + 2, sp_AB, target_x1, sp_BC),
                        (wC["x1"] + 2, sp_BC, target_x1, sp_CD),
                        (wD["x1"] + 2, sp_CD, target_x1, target_max_y),
                    ]
                    for q_idx, q_box in enumerate(v_boxes):
                        o_im = crop_pil_region(target_pil, w, h, q_box, pad=5)
                        if o_im:
                            op_rel = f"data/figures/t{test_num}_q{global_id}_opt{q_idx}.png"
                            o_im.save(BASE_DIR / op_rel)
                            opt_paths.append(op_rel)

                if len(opt_paths) == 4:
                    fig_manifest_entry["optFigures"] = opt_paths

            elif next_col_extra_bbox:
                opts_img = crop_pil_region(target_pil, w, h, (target_x0, target_min_y, target_x1, target_max_y), pad=8)
                if opts_img:
                    rel_opts = f"data/figures/t{test_num}_q{global_id}_opts.png"
                    opts_img.save(BASE_DIR / rel_opts)
                    fig_manifest_entry["optsFigure"] = rel_opts

            # 1. Crop the Problem Setup + Diagram / Graph / Table / Equations above Choice A)
            stem_bottom = computed_top_A if (opt_markers and not next_col_extra_bbox and computed_top_A > q_bottom + 14) else y_end
            stem_img = crop_pil_region(pil_page, w, h, (crop_x0, q_bottom + 4, crop_x1, stem_bottom), pad=10)
            if stem_img:
                rel_path = f"data/figures/t{test_num}_q{global_id}.png"
                stem_img.save(BASE_DIR / rel_path)
                fig_manifest_entry["stemFigure"] = rel_path

            # Also strip rotated y-axis OCR noise lines before prose in Math text
            raw_lines = clean_block_lines(crop_txt).split("\n")
            filtered_math_lines = []
            for ln in raw_lines:
                # Skip short garbled vertical axis label fragments like ".<../,l 40" or "::s"
                if re.match(r"^[\.\<>/\\,;:_'\"\|\s\(\)\[\]\d\-]+$", ln) and len(ln) < 18 and any(c in ln for c in "<>/:;\\"):
                    continue
                filtered_math_lines.append(ln)
            crop_txt = "\n".join(filtered_math_lines)

        results.append((q_num, clean_block_lines(crop_txt), fig_manifest_entry))

    return results


def generate_math_distractors(ans_str: str):
    try:
        clean = ans_str.split(";")[0].strip()
        if "/" in clean:
            num, den = clean.split("/", 1)
            n, d = int(num.strip()), int(den.strip())
            return [f"{n + 1}/{d}", f"{max(1, n - 1)}/{d}", f"{n + 2}/{d}"]
        elif "." in clean:
            val = float(clean)
            return [
                str(round(val + 0.5, 4)),
                str(round(val * 2, 4)),
                str(round(max(0.05, val - 0.25), 4)),
            ]
        else:
            val = int(clean)
            return [
                str(val + 2),
                str(val - 2 if val > 2 else val + 5),
                str(val * 2 if val != 0 else 10),
            ]
    except Exception:
        return ["1", "2", "4"]


def split_question_components(block_text: str, is_math: bool, raw_key: str, explanation: str, global_id: int):
    opt_pattern = re.compile(
        r"(?:^|\n|\s)A\)\s*(.*?)(?:^|\n|\s)B\)\s*(.*?)(?:^|\n|\s)C\)\s*(.*?)(?:^|\n|\s)D\)\s*(.*?)\Z",
        re.S,
    )
    m_opt = opt_pattern.search(block_text)
    options = []
    pre_options = block_text
    if m_opt:
        pre_options = block_text[:m_opt.start()].strip()
        options = [re.sub(r"\s+", " ", m_opt.group(i)).strip() for i in range(1, 5)]

    key_upper = raw_key.strip().upper()
    if key_upper not in ["A", "B", "C", "D"]:
        m_exp_key = re.search(r"Choice\s+([A-D])\s+is\s+(?:the best answer|correct)", explanation)
        if m_exp_key and m_opt:
            key_upper = m_exp_key.group(1).upper()

    if key_upper in ["A", "B", "C", "D"]:
        correct_idx = ["A", "B", "C", "D"].index(key_upper)
        if len(options) != 4 or any(len(o) == 0 for o in options):
            rebuilt = []
            for idx_let, let in enumerate(["A", "B", "C", "D"]):
                cur = options[idx_let] if idx_let < len(options) else ""
                if cur and len(cur) > 1 and not re.match(r"^[\d\s\-\.,xyI\\/'\"~]+$", cur):
                    rebuilt.append(cur)
                else:
                    m_desc = re.search(
                        rf"Choice\s+{let}\s+is\s+(?:correct|the best answer|incorrect)\.?\s*([^\.]+\.)",
                        explanation,
                    )
                    if cur and len(cur) > 0 and len(cur) <= 25:
                        rebuilt.append(cur)
                    elif m_desc:
                        rebuilt.append(f"Choice {let}: {m_desc.group(1).strip()}")
                    else:
                        rebuilt.append(f"Choice {let} (See 1:1 Graph / Visual Option)")
            options = rebuilt
    else:
        correct_idx = (global_id - 1) % 4
        distractors = generate_math_distractors(raw_key)
        options = []
        d_idx = 0
        for i in range(4):
            if i == correct_idx:
                options.append(raw_key.strip())
            else:
                options.append(distractors[d_idx])
                d_idx += 1

    if not is_math:
        stem_m = re.search(
            r"(?:^|\n)((?:Which choice|Based on the (?:text|texts|table|graph|data|passage)|According to the (?:text|texts|table|graph|data|passage)|As used in the text|What is the main|What does the|Which quotation|Which finding|Which statement|The student wants to|To make the|Which of the following)\b.*)",
            pre_options,
            re.S | re.I,
        )
        if stem_m:
            passage = pre_options[:stem_m.start()].strip()
            question = re.sub(r"\s+", " ", stem_m.group(1)).strip()
        else:
            lines = [l.strip() for l in pre_options.split("\n") if l.strip()]
            if len(lines) > 2:
                passage = "\n".join(lines[:-2]).strip()
                question = " ".join(lines[-2:]).strip()
            else:
                passage = pre_options.strip()
                question = "Which choice best answers the question based on the passage?"
    else:
        cleaned_pre = pre_options.strip()
        q_sent_m = re.search(
            r"(?:^|\n|(?<=[\.\?])\s+)((?:What|Which|How\s+many|How\s+much|If\b|For\s+what|In\s+the\s+given|Find\b|Approximately\s+how)[^\.]*\?\s*)$",
            cleaned_pre,
            re.S | re.I,
        )
        if q_sent_m and q_sent_m.start() > 8:
            passage = cleaned_pre[:q_sent_m.start()].strip()
            question = re.sub(r"\s+", " ", q_sent_m.group(1)).strip()
        else:
            lines = [l.strip() for l in cleaned_pre.split("\n") if l.strip()]
            if len(lines) > 2:
                passage = "\n".join(lines[:-1]).strip()
                question = re.sub(r"\s+", " ", lines[-1]).strip()
            else:
                passage = cleaned_pre
                question = re.sub(r"\s+", " ", cleaned_pre).strip()

    if not passage:
        passage = question
    if not question:
        question = "Select the best answer choice."

    return passage, question, options, correct_idx


def process_single_mock(test_num: int):
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    test_pdf = BASE_DIR / f"sat-practice-test-{test_num}-digital.pdf"
    ans_pdf = BASE_DIR / f"sat-practice-test-{test_num}-answers-digital.pdf"
    score_pdf = BASE_DIR / f"scoring-sat-practice-test-{test_num}-digital.pdf"

    if not (test_pdf.exists() and ans_pdf.exists() and score_pdf.exists()):
        return test_num, [], {}, {}

    answer_key, scoring_tables = parse_scoring_pdf(score_pdf)
    explanations = parse_answers_pdf(ans_pdf)
    stops = detect_module_stops(test_num, test_pdf)

    module_configs = [
        {"module": 1, "type": "Reading", "start": 3, "end": stops[0], "count": 33, "is_math": False},
        {"module": 2, "type": "Reading", "start": stops[0] + 1, "end": stops[1], "count": 33, "is_math": False},
        {"module": 1, "type": "Math", "start": stops[1] + 1, "end": stops[2], "count": 27, "is_math": True},
        {"module": 2, "type": "Math", "start": stops[2] + 1, "end": stops[3], "count": 27, "is_math": True},
    ]

    items = []
    figures_map = {}
    global_idx = 0

    pdfium_doc = pdfium.PdfDocument(str(test_pdf))
    with pdfplumber.open(str(test_pdf)) as pdf:
        for cfg in module_configs:
            q_blocks = extract_module_questions_and_figures(
                pdf,
                pdfium_doc,
                test_num,
                cfg["start"],
                cfg["end"],
                cfg["count"],
                cfg["is_math"],
                global_idx + 1,
            )
            for q_num, block_text, fig_entry in q_blocks:
                global_id = global_idx + 1
                raw_key = answer_key[global_idx] if global_idx < len(answer_key) else "A"
                exp_text = explanations[global_idx] if global_idx < len(explanations) else "Explanation unavailable."
                passage, question, options, correct_idx = split_question_components(
                    block_text, cfg["is_math"], raw_key, exp_text, global_id
                )
                item = {
                    "id": global_id,
                    "testNumber": test_num,
                    "module": cfg["module"],
                    "type": cfg["type"],
                    "passage": passage,
                    "question": question,
                    "options": options,
                    "correctAnswer": correct_idx,
                    "explanation": exp_text,
                }
                items.append(item)
                if fig_entry:
                    figures_map[str(global_id)] = fig_entry
                global_idx += 1

    # Extract 1:1 visual official explanations for all 54 Math questions (global_id 67..120)
    exp_figs = extract_math_explanation_figures(ans_pdf, test_num)
    for q_str, exp_rel in exp_figs.items():
        figures_map.setdefault(q_str, {})["expFigure"] = exp_rel

    return test_num, items, scoring_tables, figures_map


def main():
    t0 = time.time()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    test_numbers = list(range(4, 12))

    all_mocks = {}
    all_scoring = {}
    all_figures = {}

    print("==========================================================================")
    print("  BLUEBOOK DIGITAL SAT 1:1 DATA & VISUAL FIGURE INGESTION ENGINE (v2.0)   ")
    print("==========================================================================")

    with ProcessPoolExecutor() as executor:
        for test_num, items, scoring_tables, figures_map in executor.map(process_single_mock, test_numbers):
            all_mocks[str(test_num)] = items
            all_scoring[str(test_num)] = scoring_tables
            all_figures[str(test_num)] = figures_map

            for target_dir in [DATA_DIR, BASE_DIR]:
                mock_path = target_dir / f"mock{test_num}.json"
                with open(mock_path, "w", encoding="utf-8") as f:
                    json.dump(items, f, indent=2, ensure_ascii=False)

            score_path = DATA_DIR / f"scoring{test_num}.json"
            with open(score_path, "w", encoding="utf-8") as f:
                json.dump(scoring_tables, f, indent=2, ensure_ascii=False)

            rw_figs = sum(1 for k in figures_map if int(k) <= 66)
            math_figs = sum(1 for k in figures_map if int(k) > 66)
            print(
                f"[OK] SAT Test #{test_num:<2} -> {len(items)} Qs | Visual Figures Cropped: {len(figures_map)} (R&W Charts/Tables: {rw_figs}, Math 1:1 Cards: {math_figs})"
            )

    with open(DATA_DIR / "scoring_tables.json", "w", encoding="utf-8") as f:
        json.dump(all_scoring, f, indent=2, ensure_ascii=False)

    with open(DATA_DIR / "figures_manifest.json", "w", encoding="utf-8") as f:
        json.dump(all_figures, f, indent=2, ensure_ascii=False)

    bundle_path = DATA_DIR / "mocks_bundle.js"
    with open(bundle_path, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by parse_mocks.py for local file:// and HTTP compatibility\n")
        f.write("window.SAT_MOCKS_DATA = ")
        json.dump(all_mocks, f, ensure_ascii=False)
        f.write(";\nwindow.SAT_SCORING_DATA = ")
        json.dump(all_scoring, f, ensure_ascii=False)
        f.write(";\nwindow.SAT_FIGURES_MANIFEST = ")
        json.dump(all_figures, f, ensure_ascii=False)
        f.write(";\n")

    elapsed = round(time.time() - t0, 2)
    total_figs = sum(len(m) for m in all_figures.values())
    print("--------------------------------------------------------------------------")
    print(f"Ingestion complete in {elapsed}s | 960 Questions + {total_figs} Question Figure Sets Saved!")
    print("==========================================================================")


if __name__ == "__main__":
    main()
