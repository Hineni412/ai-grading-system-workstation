import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from db_manager import DBManager
from path_manager import get_path_manager

def build_subjective_image_packet(
    session_id: str,
    student_id: str,
    included_question_ids: list[str],
    output_dir: str | None = None
) -> dict:
    pm = get_path_manager()
    db = DBManager(pm.db_path)
    
    if output_dir is None:
        output_dir = str(pm.logs_dir / "subjective_only_dry_run" / str(session_id) / str(student_id))
        
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Fetch exam paper paths
    with db._connect() as conn:
        row = conn.execute(
            """
            SELECT ep.front_image, ep.back_image
            FROM exam_papers ep
            JOIN session_results sr ON sr.paper_id = ep.id
            JOIN students s ON s.id = sr.student_id
            WHERE sr.session_id = ? AND (s.student_code = ? OR s.id = ? OR s.name = ?)
            """,
            (session_id, student_id, student_id, student_id)
        ).fetchone()
        
    if not row:
        return {
            "image_paths": [],
            "regions": [],
            "missing_regions": included_question_ids,
            "diagnostics": [f"Exam paper not found for student {student_id}"]
        }
        
    front_path = row["front_image"]
    back_path = row["back_image"]
    
    # 2. Fetch answer regions
    with db._connect() as conn:
        region_rows = conn.execute(
            """
            SELECT page, mapped_question_id, x, y, w, h
            FROM answer_regions
            WHERE session_id = ? AND mapped_question_id IS NOT NULL
            """,
            (session_id,)
        ).fetchall()
        
    region_map = {}
    for r in region_rows:
        qid = r["mapped_question_id"]
        # In case of multiple regions for same question (e.g. multi-page or parts), collect them all
        if qid not in region_map:
            region_map[qid] = []
        region_map[qid].append(dict(r))
        
    # 3. Crop regions
    try:
        img_front = Image.open(front_path) if front_path and os.path.exists(front_path) else None
        img_back = Image.open(back_path) if back_path and os.path.exists(back_path) else None
    except Exception as e:
        return {
            "image_paths": [],
            "regions": [],
            "missing_regions": included_question_ids,
            "diagnostics": [f"Failed to open source images: {e}"]
        }
        
    regions_info = []
    missing_regions = []
    diagnostics = []
    
    cropped_images = []
    
    for qid in included_question_ids:
        matched_regions = []
        if qid in region_map:
            matched_regions.extend(region_map[qid])
        
        # also match parts like Q10(1) for Q10
        for r_qid, r_list in region_map.items():
            if r_qid.startswith(f"{qid}(") or r_qid.startswith(f"{qid}_"):
                matched_regions.extend(r_list)
                
        if not matched_regions:
            missing_regions.append(qid)
            diagnostics.append(f"No region coordinates found for {qid}")
            continue
            
        for r in matched_regions:
            page = r["page"]
            src_img = img_front if page == 'front' else img_back
            if not src_img:
                missing_regions.append(qid)
                diagnostics.append(f"Source image missing for page {page} ({qid})")
                continue
                
            x, y, w, h = r["x"], r["y"], r["w"], r["h"]
            # Add some padding
            pad = 10
            crop_box = (
                max(0, x - pad),
                max(0, y - pad),
                min(src_img.width, x + w + pad),
                min(src_img.height, y + h + pad)
            )
            
            try:
                crop = src_img.crop(crop_box)
                
                # Add label
                labeled_crop = Image.new("RGB", (crop.width, crop.height + 40), "white")
                labeled_crop.paste(crop, (0, 40))
                draw = ImageDraw.Draw(labeled_crop)
                
                # Try to use a default font, otherwise just basic text
                try:
                    font = ImageFont.truetype("arial.ttf", 24)
                except IOError:
                    font = ImageFont.load_default()
                    
                draw.text((10, 10), f"Question: {qid}", fill="black", font=font)
                
                cropped_images.append({
                    "qid": qid,
                    "img": labeled_crop
                })
            except Exception as e:
                missing_regions.append(qid)
                diagnostics.append(f"Failed to crop {qid}: {e}")
                
    if img_front: img_front.close()
    if img_back: img_back.close()
    
    if not cropped_images:
        return {
            "image_paths": [],
            "regions": [],
            "missing_regions": list(set(missing_regions)),
            "diagnostics": diagnostics
        }
        
    # 4. Stitch vertically
    # To avoid exceeding max resolution, we might chunk them if total height > 8000
    MAX_HEIGHT = 8000
    current_img = None
    current_y = 0
    max_w = 0
    stitched_paths = []
    
    def save_current_stitch(stitch_list, index):
        if not stitch_list: return None
        total_h = sum(img["img"].height for img in stitch_list)
        max_w = max(img["img"].width for img in stitch_list)
        
        composite = Image.new("RGB", (max_w, total_h), "white")
        cy = 0
        for item in stitch_list:
            composite.paste(item["img"], (0, cy))
            cy += item["img"].height
            
        out_path = os.path.join(output_dir, f"subjective_packet_{index}.jpg")
        composite.save(out_path, format="JPEG", quality=85)
        return out_path
        
    current_chunk = []
    chunk_index = 1
    
    for item in cropped_images:
        if sum(c["img"].height for c in current_chunk) + item["img"].height > MAX_HEIGHT and current_chunk:
            # save chunk
            path = save_current_stitch(current_chunk, chunk_index)
            if path:
                stitched_paths.append(path)
                for c in current_chunk:
                    regions_info.append({
                        "question_id": c["qid"],
                        "image_path": path,
                        "width": c["img"].width,
                        "height": c["img"].height,
                        "file_size_kb": os.path.getsize(path) / 1024
                    })
            chunk_index += 1
            current_chunk = []
            
        current_chunk.append(item)
        
    if current_chunk:
        path = save_current_stitch(current_chunk, chunk_index)
        if path:
            stitched_paths.append(path)
            for c in current_chunk:
                regions_info.append({
                    "question_id": c["qid"],
                    "image_path": path,
                    "width": c["img"].width,
                    "height": c["img"].height,
                    "file_size_kb": os.path.getsize(path) / 1024
                })
                
    return {
        "image_paths": stitched_paths,
        "regions": regions_info,
        "missing_regions": list(set(missing_regions)),
        "diagnostics": diagnostics
    }
