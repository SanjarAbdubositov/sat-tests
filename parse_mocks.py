#!/usr/bin/env python3
"""
Automated Data Ingestion Engine for Digital SAT Mock Test Platform (Tests 4 to 11).
Extracts passages, questions, multiple-choice options, official answer keys,
detailed answer explanations, and raw-to-scale score conversion tables from
College Board Digital SAT PDFs in the workspace directory.
"""

import json
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import pypdf
import pdfplumber

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# Fallback module stop-page indices (0-based) for Tests 4-11
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
    Extracts all 120 detailed question explanations and rationales from the
    '-answers-digital.pdf' file.
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
        # Strip trailing module header transitions
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


def detect_module_stops(test_num: int, test_path: Path):
    """
    Finds the 4 module ending pages ('If you finish before time is called').
    """
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
    txt = re.sub(r"The questions in this section address a number of important reading and writing skills.*?single best answer\.", "", txt, flags=re.S | re.I)
    lines = []
    for line in txt.split("\n"):
        s = line.strip()
        if not s:
            continue
        if re.match(r"^(?:Modu|ule|Module|Reading and Writ|ing|Math|DIRECTIONS|\d+\s+QUESTIONS)$", s, re.I):
            continue
        if re.match(r"^(?:The questions in this section|question includes one or more|and question carefully|All questions in this section|single best answer|ich may include a table|ber of important reading|best answer to the question|oice with four answer)", s, re.I):
            continue
        if re.match(r"^(?:Unauthorized copying|CONTINUE|CO\s*N\s*T\s*I\s*N\s*U\s*E|STOP|If you finish before time|Do not turn to any other)", s, re.I):
            continue
        if re.match(r"^[\.\-~_\s\\/'\"\|,]+$", s) and "_______" not in s:
            continue
        if re.match(r"^1\s+1$", s) or re.match(r"^2\s+2$", s):
            continue
        lines.append(s)
    return "\n".join(lines)


def extract_module_question_blocks(pdf, start_page: int, end_page: int, expected_count: int, is_math: bool):
    """
    Uses geometric column & margin coordinate detection in pdfplumber to isolate
    every question (1..33 for Reading/Writing, 1..27 for Math) in exact order.
    """
    columns = []
    for p_idx in range(start_page, end_page + 1):
        p = pdf.pages[p_idx]
        w, h = p.width, p.height
        words = p.extract_words()
        page_str = " ".join(wd["text"] for wd in words[:60])
        if is_math and ("For multiple-choice questions" in page_str or "important math skills" in page_str):
            continue
        if "No Test Material On This Page" in page_str:
            continue

        for col_idx, (x_min, x_max, qx_min, qx_max) in enumerate([
            (0, w / 2.0, 32, 68),
            (w / 2.0, w, 310, 348),
        ]):
            col_words = sorted(
                [wd for wd in words if x_min <= wd["x0"] < x_max and 55 <= wd["top"] <= h - 42],
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
                "h": h,
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

    q_blocks = []
    for i, (q_num, c_idx, top, bottom) in enumerate(matched_locs):
        col = columns[c_idx]
        p = pdf.pages[col["p_idx"]]
        if i + 1 < len(matched_locs) and matched_locs[i + 1][1] == c_idx:
            y_end = matched_locs[i + 1][2] - 2
            crop_txt = p.crop((col["x_min"], bottom + 1, col["x_max"], max(bottom + 5, y_end))).extract_text() or ""
        else:
            crop_txt = p.crop((col["x_min"], bottom + 1, col["x_max"], col["h"] - 42)).extract_text() or ""
            # Only check top of next column if D) was not yet reached in current column
            if "D)" not in crop_txt and i + 1 < len(matched_locs):
                next_c_idx = matched_locs[i + 1][1]
                next_top = matched_locs[i + 1][2]
                if next_c_idx == c_idx + 1 and next_top > 120:
                    next_col = columns[next_c_idx]
                    p_next = pdf.pages[next_col["p_idx"]]
                    extra_txt = p_next.crop((next_col["x_min"], 55, next_col["x_max"], next_top - 4)).extract_text() or ""
                    if "D)" in extra_txt and "DIRECTIONS" not in extra_txt:
                        crop_txt = crop_txt + "\n" + extra_txt
        q_blocks.append((q_num, clean_block_lines(crop_txt)))
    return q_blocks


def generate_math_distractors(ans_str: str):
    """Generates 3 plausible numerical distractors for grid-in Math questions."""
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
    """
    Splits a question block into (passage, question, options, correctAnswer).
    Ensures every item strictly conforms to the 4-option schema.
    """
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
                if cur and len(cur) > 1:
                    rebuilt.append(cur)
                else:
                    m_desc = re.search(
                        rf"Choice\s+{let}\s+is\s+(?:correct|the best answer|incorrect)\.?\s*([^\.]+\.)",
                        explanation,
                    )
                    if m_desc:
                        rebuilt.append(f"Choice {let}: {m_desc.group(1).strip()}")
                    else:
                        rebuilt.append(f"Choice {let} (Graph / Table Representation)")
            options = rebuilt
    else:
        # Student-Produced Response (SPR) Math Question -> convert to 4-choice format
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
    test_pdf = BASE_DIR / f"sat-practice-test-{test_num}-digital.pdf"
    ans_pdf = BASE_DIR / f"sat-practice-test-{test_num}-answers-digital.pdf"
    score_pdf = BASE_DIR / f"scoring-sat-practice-test-{test_num}-digital.pdf"

    if not (test_pdf.exists() and ans_pdf.exists() and score_pdf.exists()):
        return test_num, [], {}

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
    global_idx = 0

    with pdfplumber.open(str(test_pdf)) as pdf:
        for cfg in module_configs:
            q_blocks = extract_module_question_blocks(
                pdf, cfg["start"], cfg["end"], cfg["count"], cfg["is_math"]
            )
            for q_num, block_text in q_blocks:
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
                global_idx += 1

    return test_num, items, scoring_tables


def main():
    t0 = time.time()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    test_numbers = list(range(4, 12))

    all_mocks = {}
    all_scoring = {}

    print("=================================================================")
    print("  BLUEBOOK DIGITAL SAT AUTOMATED DATA INGESTION ENGINE (v1.0)    ")
    print("=================================================================")

    with ProcessPoolExecutor() as executor:
        for test_num, items, scoring_tables in executor.map(process_single_mock, test_numbers):
            all_mocks[str(test_num)] = items
            all_scoring[str(test_num)] = scoring_tables

            # Write mock{N}.json into both ./data/ and root directory
            for target_dir in [DATA_DIR, BASE_DIR]:
                mock_path = target_dir / f"mock{test_num}.json"
                with open(mock_path, "w", encoding="utf-8") as f:
                    json.dump(items, f, indent=2, ensure_ascii=False)

            score_path = DATA_DIR / f"scoring{test_num}.json"
            with open(score_path, "w", encoding="utf-8") as f:
                json.dump(scoring_tables, f, indent=2, ensure_ascii=False)

            rw_cnt = sum(1 for x in items if x["type"] == "Reading")
            m_cnt = sum(1 for x in items if x["type"] == "Math")
            print(f"[OK] SAT Practice Test #{test_num:<2} -> {len(items)} questions (RW: {rw_cnt}, Math: {m_cnt}) | Saved mock{test_num}.json")

    # Save combined scoring tables JSON
    combined_scoring_path = DATA_DIR / "scoring_tables.json"
    with open(combined_scoring_path, "w", encoding="utf-8") as f:
        json.dump(all_scoring, f, indent=2, ensure_ascii=False)

    # Also write a JS bundle for zero-CORS local file:// protocol execution
    bundle_path = DATA_DIR / "mocks_bundle.js"
    with open(bundle_path, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by parse_mocks.py for local file:// and HTTP compatibility\n")
        f.write("window.SAT_MOCKS_DATA = ")
        json.dump(all_mocks, f, ensure_ascii=False)
        f.write(";\nwindow.SAT_SCORING_DATA = ")
        json.dump(all_scoring, f, ensure_ascii=False)
        f.write(";\n")

    elapsed = round(time.time() - t0, 2)
    print("-----------------------------------------------------------------")
    print(f"Ingestion complete in {elapsed}s. Total questions extracted: {sum(len(v) for v in all_mocks.values())}")
    print("=================================================================")


if __name__ == "__main__":
    main()
