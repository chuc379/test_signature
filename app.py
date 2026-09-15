import os
import re
import json
import io
import httpx
import pymupdf as fitz  # Đã sửa lại chuẩn theo khuyến cáo
from PIL import Image, ImageDraw, ImageFont
from huggingface_hub import HfApi
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import JSONResponse
import gradio as gr

# --- CẤU HÌNH HUGGING FACE DATASET ---
HF_REPO_ID = "chuong090703/sinature"
HF_TOKEN = os.getenv("HF_TOKEN", "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")

hf_api = HfApi()

app = FastAPI(title="Twenty CRM Real Signing Mock Server")

# Thêm route gốc để tránh lỗi 404 khi Health Check hệ thống
@app.get("/")
async def root_health_check():
    return {"status": "ok", "message": "Service is running successfully!"}

TWENTY_CRM_WEBHOOK_URL = os.getenv(
    "TWENTY_CRM_WEBHOOK_URL", 
    "https://tinasoft.tinacrm.tinasoft.io/webhooks/workflows/14d89b50-1301-4711-8f12-c8741562ca61/ec081d02-c556-4c3b-a1a6-d33d1c4cf03b"
)

# Đường dẫn ảnh chữ ký tay
SIGNATURE_IMAGE_URL = "https://i.pinimg.com/736x/43/da/7d/43da7d45279d0f4c042a2bf2079f918b.jpg"


def extract_url_from_text(raw_text: str) -> str:
    if not raw_text:
        return ""
    try:
        data = json.loads(raw_text)
        if isinstance(data, dict):
            for field in ["file", "files", "linkFile", "url", "fileUrl"]:
                val = data.get(field)
                if isinstance(val, str) and val.strip().startswith("["):
                    try:
                        val = json.loads(val)
                    except Exception:
                        pass
                if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                    if "url" in val[0]:
                        return val[0]["url"]
                elif isinstance(val, str) and val.startswith("http"):
                    return val
    except Exception:
        pass

    match = re.search(r'https?://[^\s"\'\\]+', raw_text)
    if match:
        url = match.group(0)
        url = url.split('&quot;')[0].split('"')[0].rstrip(']}')
        return url
    return ""


def extract_exact_hop_dong_id(raw_text: str) -> str:
    match = re.search(r'"hopDongThietKe[IdlL]+"\s*:\s*"([^"]+)"', raw_text, re.IGNORECASE)
    if match:
        val = match.group(1).strip()
        if val and val.lower() != "id":
            return val
    uuids = re.findall(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', raw_text)
    if uuids:
        return uuids[-1]
    return "default_id"


def create_signature_image_bytes(signer_name: str) -> bytes:
    """Tạo ảnh con dấu căn giữa toàn bộ, nền đỏ, chữ ký tay màu đen"""
    img_width, img_height = 400, 260
    image = Image.new("RGBA", (img_width, img_height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)

    # Vẽ khung con dấu nền đỏ nhạt, viền đỏ
    draw.rectangle([10, 10, img_width - 10, img_height - 10], fill=(255, 235, 235), outline=(204, 0, 0), width=3)

    try:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if not os.path.exists(font_path):
            font_path = "arial.ttf"
        font_title = ImageFont.truetype(font_path, 15)
        font_body = ImageFont.truetype(font_path, 13)
    except Exception:
        font_title = ImageFont.load_default()
        font_body = ImageFont.load_default()

    text_title = "✔ ĐÃ KÝ DUYỆT DIGITAL"
    text_name = f"Người ký: {signer_name}"
    text_date = "Ngày: 2026-09-14"

    def get_centered_x(text, font):
        try:
            bbox = font.getbbox(text)
            w = bbox[2] - bbox[0]
        except Exception:
            w = len(text) * 7
        return (img_width - w) / 2

    x_title = get_centered_x(text_title, font_title)
    draw.text((x_title, 20), text_title, fill=(204, 0, 0), font=font_title)

    try:
        response = httpx.get(SIGNATURE_IMAGE_URL, timeout=10.0)
        if response.status_code == 200:
            sig_img = Image.open(io.BytesIO(response.content)).convert("RGBA")
            sig_width, sig_height = 160, 70
            sig_img = sig_img.resize((sig_width, sig_height))
            
            datas = sig_img.getdata()
            new_data = []
            for item in datas:
                if item[0] > 200 and item[1] > 200 and item[2] > 200:
                    new_data.append((255, 255, 255, 0))
                else:
                    new_data.append((0, 0, 0, item[3] if len(item) > 3 else 255))
            sig_img.putdata(new_data)
            
            x_sig = (img_width - sig_width) / 2
            image.paste(sig_img, (int(x_sig), 55), sig_img)
    except Exception as e:
        print(f"[SIGNATURE IMAGE ERROR] Không thể tải ảnh chữ ký tay: {e}")

    x_name = get_centered_x(text_name, font_body)
    draw.text((x_name, 175), text_name, fill=(204, 0, 0), font=font_body)

    x_date = get_centered_x(text_date, font_body)
    draw.text((x_date, 205), text_date, fill=(204, 0, 0), font=font_body)

    output_io = io.BytesIO()
    image.save(output_io, format="PNG")
    return output_io.getvalue()


def apply_digital_signature_to_pdf(input_pdf_bytes: bytes, signer_name: str) -> bytes:
    try:
        doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
        page = doc[-1]
        
        rect = page.rect
        x1, y1 = rect.width - 240, rect.height - 160
        x2, y2 = rect.width - 20, rect.height - 20
        sign_rect = fitz.Rect(x1, y1, x2, y2)

        stamp_bytes = create_signature_image_bytes(signer_name)
        page.insert_image(sign_rect, stream=stamp_bytes)

        signed_bytes = doc.tobytes()
        doc.close()
        return signed_bytes
    except Exception as e:
        print(f"[PDF ERROR] Chèn con dấu thất bại: {e}")
        return None


def upload_to_hf_dataset(file_bytes: bytes, filename: str) -> str:
    try:
        if not HF_TOKEN or HF_TOKEN.strip() == "":
            print("[HF UPLOAD ERROR] Chưa cấu hình HF_TOKEN!")
            return ""

        path_in_repo = f"signed_files/{filename}"
        hf_api.upload_file(
            path_or_fileobj=file_bytes,
            path_in_repo=path_in_repo,
            repo_id=HF_REPO_ID,
            repo_type="dataset",
            token=HF_TOKEN
        )
        return f"https://huggingface.co/datasets/{HF_REPO_ID}/resolve/main/{path_in_repo}"
    except Exception as e:
        print(f"[HF UPLOAD ERROR] Lỗi upload lên HuggingFace: {e}")
        return ""
    

async def process_signing_task(file_url: str, hop_dong_id: str, webhook_url: str):
    is_signed = False
    public_signed_url = ""

    print(f"--> [DOWNLOADING] Link file thực tế: '{file_url}'")

    if file_url and file_url.startswith("http"):
        async with httpx.AsyncClient() as client:
            try:
                res = await client.get(file_url, follow_redirects=True, timeout=30.0)
                if res.status_code == 200:
                    signed_bytes = apply_digital_signature_to_pdf(
                        input_pdf_bytes=res.content, 
                        signer_name="Trưởng Phòng Bổ Sung"
                    )
                    
                    if signed_bytes:
                        output_filename = f"signed_{hop_dong_id}.pdf"
                        public_signed_url = upload_to_hf_dataset(signed_bytes, output_filename)
                        
                        if public_signed_url:
                            is_signed = True
                            print(f"--> [HF PUBLIC LINK THÀNH CÔNG]: {public_signed_url}")
                else:
                    print(f"--> [DOWNLOAD ERROR] Status Code từ server chứa file: {res.status_code}")
            except Exception as e:
                print(f"--> [DOWNLOAD ERROR] Lỗi tải/xử lý file: {e}")

    payload = {
        "linkFile": public_signed_url,
        "isSuccess": is_signed,
        "hopDongThietKeId": hop_dong_id,
        "hopDongThietKeld": hop_dong_id
    }
    
    print(f"--> [PAYLOAD GỬI CRM]: {json.dumps(payload, ensure_ascii=False, indent=2)}")

    if webhook_url:
        async with httpx.AsyncClient() as client:
            try:
                res = await client.post(webhook_url, json=payload, timeout=10.0)
                print(f"--> [WEBHOOK RESULT] Status Code từ CRM: {res.status_code}")
                print(f"--> [WEBHOOK RESPONSE BODY]: {res.text}")
            except Exception as e:
                print(f"--> [WEBHOOK ERROR] Lỗi bắn webhook: {e}")


@app.post("/api/v1/sign")
async def mock_sign_request(request: Request, background_tasks: BackgroundTasks):
    raw_body_bytes = await request.body()
    raw_body_str = raw_body_bytes.decode("utf-8", errors="ignore")

    body_json = {}
    try:
        parsed_data = json.loads(raw_body_str)
        if isinstance(parsed_data, dict):
            body_json = parsed_data
    except Exception:
        pass

    hop_dong_id = (
        body_json.get("hopDongThietKeId") 
        or body_json.get("hopDongThietKeld") 
        or extract_exact_hop_dong_id(raw_body_str)
    )

    file_url = extract_url_from_text(raw_body_str)
    webhook_url = body_json.get("webhook_url") if isinstance(body_json, dict) else None
    if not webhook_url:
        webhook_url = TWENTY_CRM_WEBHOOK_URL

    background_tasks.add_task(
        process_signing_task, 
        file_url, 
        hop_dong_id, 
        webhook_url
    )

    return JSONResponse(
        status_code=200,
        content={
            "message": "Đã tiếp nhận yêu cầu ký file thành công.",
            "hopDongThietKeId": hop_dong_id,
            "hopDongThietKeld": hop_dong_id,
            "status": "PROCESSING"
        }
    )

with gr.Blocks(title="Twenty CRM Signing API") as demo:
    gr.Markdown("# 🖋️ Twenty CRM PDF Signing Backend")

# Mount Gradio đè lên route khác hoặc giữ nguyên path phụ, đồng thời route chính "/" ở trên sẽ lo phần health check
app = gr.mount_gradio_app(app, demo, path="/gradio")
