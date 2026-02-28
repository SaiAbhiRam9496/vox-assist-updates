from backend.models.models import DesignCreate, DesignBase
from bson import ObjectId
from backend.services.generation_service import generation_service
from backend.database.connection import get_database
from backend.utils.auth_utils import get_current_user_uid
from backend.utils.rate_limit import limiter
from datetime import datetime
from typing import List
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Request
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

async def process_generation_job(job_id: str, prompt: str, uid: str):
    db = get_database()
    try:
        result = await generation_service.generate_layout(prompt)
        if not result["success"]:
            await db.jobs.update_one(
                {"_id": ObjectId(job_id)}, 
                {"$set": {"status": "failed", "error": result.get("error", "Unknown ML Error")}}
            )
            return
            
        design_doc = {
            "user_id": uid,
            "prompt": prompt,
            "layout_data": result["layout"],
            "spec_data": result["spec"],
            "model_url": result.get("model_url"),
            "score": result.get("score", 0),
            "stats": result.get("stats", {}),
            "created_at": datetime.utcnow(),
            "design_id": result.get("design_id"),
            "name": "Untitled Project",
            "description": "",
            "tags": [],
            "is_deleted": False,
            "parent_id": None
        }
        
        new_design = await db.designs.insert_one(design_doc)
        
        await db.jobs.update_one(
            {"_id": ObjectId(job_id)}, 
            {"$set": {
                "status": "completed", 
                "result": {**result, "db_id": str(new_design.inserted_id)}
            }}
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        await db.jobs.update_one({"_id": ObjectId(job_id)}, {"$set": {"status": "failed", "error": str(e)}})


@router.post("/generate", response_model=dict)
@limiter.limit("5/minute")
async def generate_layout(
    request: Request,
    design: DesignCreate, 
    background_tasks: BackgroundTasks,
    uid: str = Depends(get_current_user_uid)
):
    """
    Queue a layout generation job.
    """
    db = get_database()

    job_doc = {
        "user_id": uid,
        "prompt": design.prompt,
        "status": "pending",
        "created_at": datetime.utcnow()
    }
    job = await db.jobs.insert_one(job_doc)
    job_id = str(job.inserted_id)

    background_tasks.add_task(process_generation_job, job_id, design.prompt, uid)
    
    return {"success": True, "job_id": job_id}

@router.get("/jobs/{job_id}", response_model=dict)
async def get_job_status(job_id: str, uid: str = Depends(get_current_user_uid)):
    db = get_database()
    job = await db.jobs.find_one({"_id": ObjectId(job_id), "user_id": uid})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    job["_id"] = str(job["_id"])
    return job

@router.get("/designs", response_model=List[dict])
async def get_all_designs(limit: int = 10):
    """
    Get generic designs for the Explore page. Limited to 10 latest.
    """
    db = get_database()
    cursor = db.designs.find({"is_deleted": {"$ne": True}}).sort("created_at", -1).limit(limit)
    designs = await cursor.to_list(length=limit)
    
    # Convert ObjectId
    for d in designs:
        d["_id"] = str(d["_id"])
        
    return designs

@router.get("/my-designs", response_model=List[dict])
async def get_my_designs(uid: str = Depends(get_current_user_uid)):
    """
    Get designs for the current user.
    Returns UNIQUE prompts only (latest version), limited to 30.
    """
    db = get_database()
    
    pipeline = [
        {"$match": {"user_id": uid, "is_deleted": {"$ne": True}}},
        {"$sort": {"created_at": -1}},
        {"$group": {
            "_id": "$prompt",
            "doc": {"$first": "$$ROOT"}
        }},
        {"$replaceRoot": {"newRoot": "$doc"}},
        {"$sort": {"created_at": -1}},
        {"$limit": 30}
    ]
    
    cursor = db.designs.aggregate(pipeline)
    designs = await cursor.to_list(length=30)
    
    for d in designs:
        d["_id"] = str(d["_id"])
        
    return designs

from pydantic import BaseModel
from typing import Optional

class DesignUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    is_deleted: Optional[bool] = None

@router.put("/designs/{design_id}", response_model=dict)
async def update_design(
    design_id: str,
    update_data: DesignUpdate,
    uid: str = Depends(get_current_user_uid)
):
    db = get_database()
    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    
    if not update_dict:
        return {"success": True, "message": "No fields to update"}
        
    result = await db.designs.update_one(
        {"_id": ObjectId(design_id), "user_id": uid},
        {"$set": update_dict}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Design not found or unauthorized")
        
    return {"success": True, "message": "Design updated"}

@router.post("/designs/{design_id}/duplicate", response_model=dict)
async def duplicate_design(
    design_id: str,
    uid: str = Depends(get_current_user_uid)
):
    db = get_database()
    original = await db.designs.find_one({"_id": ObjectId(design_id), "user_id": uid})
    
    if not original:
        raise HTTPException(status_code=404, detail="Design not found or unauthorized")
        
    new_design = dict(original)
    del new_design["_id"]
    new_design["name"] = f"{original.get('name', 'Untitled Project')} (Copy)"
    new_design["created_at"] = datetime.utcnow()
    new_design["parent_id"] = str(original["_id"])
    
    res = await db.designs.insert_one(new_design)
    return {"success": True, "new_id": str(res.inserted_id)}

@router.get("/health")
def health_check():
    return {"status": "ok"}


@router.post("/blueprint")
async def generate_blueprint(request: Request):
    """
    Generate a professional branded PDF with 3D screenshot and 2D floorplan.
    """
    import tempfile
    import io
    import base64
    import sys
    import os
    from datetime import datetime
    from fastapi.responses import StreamingResponse
    from shapely.geometry import Polygon, MultiPolygon
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    import matplotlib.image as mpimg
    import numpy as np

    body = await request.json()
    layout_data = body.get("layout_data")
    screenshot_b64 = body.get("screenshot_base64", "")
    room_summary = body.get("room_summary", [])
    score = body.get("score", 0)
    prompt = body.get("prompt", "")

    # ── Decode 3D screenshot ──
    screenshot_img = None
    if screenshot_b64 and "," in screenshot_b64:
        try:
            img_data = base64.b64decode(screenshot_b64.split(",")[1])
            screenshot_img = mpimg.imread(io.BytesIO(img_data), format='png')
        except Exception as e:
            logging.warning(f"Failed to decode screenshot: {e}")
            pass

    # ── Reconstruct room polygons for 2D floorplan ──
    rooms = {}
    if layout_data and isinstance(layout_data, dict) and "rooms" in layout_data:
        for room_name, geojson_dict in layout_data["rooms"].items():
            try:
                if not isinstance(geojson_dict, dict) or "coordinates" not in geojson_dict:
                    continue
                coords = geojson_dict["coordinates"]
                geom_type = geojson_dict.get("type", "Polygon")
                if geom_type == "Polygon" and coords:
                    if isinstance(coords[0], (list, tuple)) and not isinstance(coords[0][0], (list, tuple)):
                        poly = Polygon(coords)
                    else:
                        poly = Polygon(coords[0])
                    if poly and not poly.is_empty:
                        rooms[room_name] = poly
            except Exception as e:
                logging.warning(f"Failed to reconstruct room {room_name}: {e}")
                continue

    # Reconstruct doors
    doors = None
    if layout_data and layout_data.get("doors"):
        try:
            d = layout_data["doors"]
            if isinstance(d, dict) and "coordinates" in d:
                dtype = d.get("type", "Polygon")
                coords = d["coordinates"]
                if dtype == "Polygon" and coords:
                    if isinstance(coords[0], (list, tuple)) and not isinstance(coords[0][0], (list, tuple)):
                        doors = Polygon(coords)
                    else:
                        doors = Polygon(coords[0])
                elif dtype == "MultiPolygon" and coords:
                    from shapely.ops import unary_union as uu
                    polys = []
                    for ring in coords:
                        if isinstance(ring[0], (list, tuple)) and not isinstance(ring[0][0], (list, tuple)):
                            polys.append(Polygon(ring))
                        else:
                            polys.append(Polygon(ring[0]))
                    doors = uu([p for p in polys if p and not p.is_empty])
        except Exception as e:
            logging.warning(f"Failed to reconstruct doors: {e}")
            doors = None

    entrance = None
    if layout_data and layout_data.get("entrance"):
        try:
            e = layout_data["entrance"]
            if isinstance(e, dict) and "coordinates" in e:
                coords = e["coordinates"]
                if isinstance(coords[0], (list, tuple)) and not isinstance(coords[0][0], (list, tuple)):
                    entrance = Polygon(coords)
                else:
                    entrance = Polygon(coords[0])
        except Exception as e:
            logging.warning(f"Failed to reconstruct entrance: {e}")
            entrance = None

    # ── Compose branded PDF ──
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        with PdfPages(tmp_path) as pdf:

            # ═══════════════════════════════════════
            # PAGE 1: Header + 3D Screenshot + Room Table
            # ═══════════════════════════════════════
            fig = plt.figure(figsize=(11.69, 16.54), facecolor='white')  # A3

            # ── Styled Logo: bold "VOX" + light "ASSIST" ──
            fig.text(0.465, 0.965, 'VOX', fontsize=40, fontweight='black',
                     ha='right', va='top', color='#1a1a2e',
                     fontfamily='sans-serif')
            fig.text(0.47, 0.965, 'ASSIST', fontsize=40, fontweight='light',
                     ha='left', va='top', color='#9ca3af',
                     fontfamily='sans-serif')
            fig.text(0.5, 0.94, 'AI-Generated Architectural Layout',
                     fontsize=12, ha='center', va='top', color='#6b7280',
                     fontstyle='italic')

            # Thin separator line
            line_ax = fig.add_axes([0.08, 0.93, 0.84, 0.001])
            line_ax.axhline(0, color='#d1d5db', linewidth=2)
            line_ax.axis('off')

            # ── Meta info (fixed Y positions with room for prompt) ──
            now = datetime.now().strftime("%B %d, %Y  •  %I:%M %p")
            meta_y = 0.92
            fig.text(0.08, meta_y, f'Generated: {now}', fontsize=9, color='#9ca3af')
            fig.text(0.92, meta_y, f'Score: {round(score * 100) if score < 1 else round(score)}%',
                     fontsize=12, fontweight='bold', ha='right', color='#10b981')

            # Prompt - wrap long text to 2 lines max
            prompt_y = meta_y - 0.018
            if prompt:
                max_chars = 90
                line1 = prompt[:max_chars]
                line2 = prompt[max_chars:max_chars * 2]
                if len(prompt) > max_chars * 2:
                    line2 = line2[:max_chars - 3] + '...'
                display_prompt = line1 + ('\n' + line2 if line2 else '')
                fig.text(0.08, prompt_y, f'"{display_prompt}"',
                         fontsize=8, color='#6b7280', fontstyle='italic',
                         verticalalignment='top')

            # ── 3D Screenshot (LARGER - takes up ~50% of page) ──
            screenshot_bottom = 0.38  # Bottom of screenshot area
            if screenshot_img is not None:
                ax_3d = fig.add_axes([0.04, screenshot_bottom, 0.92, 0.50])
                ax_3d.imshow(screenshot_img)
                ax_3d.axis('off')
                # Subtle border
                for spine in ax_3d.spines.values():
                    spine.set_visible(True)
                    spine.set_color('#e5e7eb')
                    spine.set_linewidth(1.5)

            # ── Room Details Table ──
            if room_summary:
                table_top = screenshot_bottom - 0.02
                fig.text(0.5, table_top, 'ROOM SPECIFICATIONS',
                         fontsize=12, fontweight='bold', ha='center',
                         color='#374151', va='top')

                table_area_top = table_top - 0.025
                ax_table = fig.add_axes([0.1, 0.05, 0.8, table_area_top - 0.05])
                ax_table.axis('off')

                # Table data
                cols = ['Room', 'Area (sqft)', 'Area (sqm)']
                col_widths = [0.5, 0.25, 0.25]

                table_data = []
                for r in room_summary:
                    table_data.append([
                        f"  {r['name']}",
                        str(r.get('area_sqft', '-')),
                        str(r.get('area_sqm', '-'))
                    ])

                # Total row
                total_sqft = sum(r.get('area_sqft', 0) for r in room_summary)
                total_sqm = sum(r.get('area_sqm', 0) for r in room_summary)
                table_data.append(['  TOTAL', str(total_sqft), str(total_sqm)])

                table = ax_table.table(
                    cellText=table_data,
                    colLabels=cols,
                    colWidths=col_widths,
                    loc='upper center',
                    cellLoc='center'
                )
                table.auto_set_font_size(False)
                table.set_fontsize(10)
                table.scale(1, 1.8)

                # Style header row
                for j in range(len(cols)):
                    cell = table[0, j]
                    cell.set_facecolor('#1a1a2e')
                    cell.set_text_props(color='white', fontweight='bold')
                    cell.set_edgecolor('#1a1a2e')

                # Style data rows
                for i in range(len(table_data)):
                    for j in range(len(cols)):
                        cell = table[i + 1, j]
                        cell.set_edgecolor('#e5e7eb')
                        if i % 2 == 0:
                            cell.set_facecolor('#f9fafb')
                        if i == len(table_data) - 1:  # Total row
                            cell.set_facecolor('#f3f4f6')
                            cell.set_text_props(fontweight='bold')

                # First column left-align
                for i in range(len(table_data) + 1):
                    table[i, 0].set_text_props(ha='left')

            # Footer
            fig.text(0.5, 0.015, 'Generated by VOX ASSIST  •  voxassist.com',
                     fontsize=8, ha='center', color='#9ca3af')

            pdf.savefig(fig)
            plt.close(fig)

            # ═══════════════════════════════════════
            # PAGE 2: 2D Floorplan (if rooms available)
            # ═══════════════════════════════════════
            if rooms:
                engine_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine")
                if engine_dir not in sys.path:
                    sys.path.insert(0, engine_dir)
                from floorplan_2d_visualizer import draw_2d_floorplan

                # Generate 2D floorplan to a temp PNG, then embed in PDF page
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fp_tmp:
                    fp_path = fp_tmp.name

                layout_for_viz = {
                    "rooms": rooms,
                    "doors": doors,
                    "entrance": entrance,
                    "corridors": None,
                    "adjacency": layout_data.get("adjacency", []) if layout_data else [],
                }
                draw_2d_floorplan(layout_for_viz, filename=fp_path)

                fig2 = plt.figure(figsize=(11.69, 16.54), facecolor='white')
                fig2.text(0.5, 0.96, 'VOX ASSIST', fontsize=28, fontweight='bold',
                         ha='center', va='top', color='#1a1a2e')
                fig2.text(0.5, 0.94, '2D Architectural Blueprint',
                         fontsize=12, ha='center', va='top', color='#6b7280')

                line_ax2 = fig2.add_axes([0.08, 0.93, 0.84, 0.001])
                line_ax2.axhline(0, color='#d1d5db', linewidth=2)
                line_ax2.axis('off')

                try:
                    fp_img = mpimg.imread(fp_path)
                    ax_fp = fig2.add_axes([0.04, 0.04, 0.92, 0.87])
                    ax_fp.imshow(fp_img)
                    ax_fp.axis('off')
                except Exception:
                    pass
                finally:
                    if os.path.exists(fp_path):
                        os.unlink(fp_path)

                fig2.text(0.5, 0.02, 'Generated by VOX ASSIST  •  voxassist.com',
                         fontsize=8, ha='center', color='#9ca3af')

                pdf.savefig(fig2)
                plt.close(fig2)

        with open(tmp_path, "rb") as f:
            pdf_bytes = f.read()

        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=voxassist_blueprint.pdf"}
        )
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
