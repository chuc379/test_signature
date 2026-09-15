import os
import re
import json
import io
import httpx
import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont
from huggingface_hub import HfApi
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import gradio as gr

# --- CƠ CHẾ LOAD ENV CHO CẢ LOCAL VÀ CLOUD ---
try:
    from dotenv import load_dotenv
    dotenv_loaded = load_dotenv()
    print(f"--> [ENV CONFIG] Đã nạp file .env thành công? {dotenv_loaded}")
except ImportError:
    print("--> [ENV CONFIG] Thư viện python-dotenv chưa được cài đặt, bỏ qua load file .env.")

# --- CẤU HÌNH TỪ BIẾN MÔI TRƯỜNG (ENV) ---
HF_REPO_ID = os.getenv("HF_REPO_ID", "chuong090703/sinature")
HF_TOKEN = os.getenv("HF_TOKEN")

TWENTY_CRM_WEBHOOK_URL = os.getenv(
    "TWENTY_CRM_WEBHOOK_URL", 
    "https://tinasoft.tinacrm.tinasoft.io/webhooks/workflows/14d89b50-1301-4711-8f12-c8741562ca61/ec081d02-c556-4c3b-a1a6-d33d1c4cf03b"
)

SIGNATURE_IMAGE_URL = os.getenv(
    "SIGNATURE_IMAGE_URL", 
    "https://i.pinimg.com/736x/43/da/7d/43da7d45279d0f4c042a2bf2079f918b.jpg"
)

# --- IN RA CONSOLE ĐỂ KIỂM TRA TRẠNG THÁI BIẾN MÔI TRƯỜNG ---
print("================ [CẤU HÌNH HỆ THỐNG] ================")
print(f"👉 HF_REPO_ID          : {HF_REPO_ID}")
print(f"👉 HF_TOKEN hiện tại   : {'[ĐÃ CÓ TOKEN - ' + HF_TOKEN[:6] + '...]' if HF_TOKEN and len(HF_TOKEN) > 5 else '[CHƯA CÓ HOẶC RỖNG!]'}")
print(f"👉 TWENTY_CRM_WEBHOOK  : {TWENTY_CRM_WEBHOOK_URL}")
print("=====================================================")

hf_api = HfApi()

app = FastAPI(title="Twenty CRM Real Signing Mock Server")


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

    print(f"--> [DOWNLOADING] Link file thực tế: '{file_url}'", flush=True)

    if file_url and file_url.startswith("http"):
        async with httpx.AsyncClient() as client:
            try:
                res = await client.get(file_url, follow_redirects=True, timeout=30.0)
                print(f"--> [DOWNLOAD STATUS] Mã phản hồi tải file: {res.status_code}", flush=True)
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
                            print(f"--> [HF PUBLIC LINK THÀNH CÔNG]: {public_signed_url}", flush=True)
                else:
                    print(f"--> [DOWNLOAD ERROR] Status Code từ server chứa file: {res.status_code}", flush=True)
            except Exception as e:
                print(f"--> [DOWNLOAD ERROR] Lỗi tải/xử lý file: {e}", flush=True)

    payload = {
        "linkFile": public_signed_url,
        "isSuccess": is_signed,
        "hopDongThietKeId": hop_dong_id,
        "hopDongThietKeld": hop_dong_id
    }
    
    print(f"--> [PAYLOAD GỬI CRM]: {json.dumps(payload, ensure_ascii=False, indent=2)}", flush=True)

    if webhook_url:
        async with httpx.AsyncClient() as client:
            try:
                res = await client.post(webhook_url, json=payload, timeout=10.0)
                print(f"--> [WEBHOOK RESULT] Status Code từ CRM: {res.status_code}", flush=True)
                print(f"--> [WEBHOOK RESPONSE BODY]: {res.text}", flush=True)
            except Exception as e:
                print(f"--> [WEBHOOK ERROR] Lỗi bắn webhook: {e}", flush=True)


@app.post("/api/v1/sign")
async def mock_sign_request(request: Request):
    print("================ [NHẬN REQUEST MỚI TỪ CRM] ================", flush=True)
    try:
        raw_body_bytes = await request.body()
        raw_body_str = raw_body_bytes.decode("utf-8", errors="ignore")
        print(f"👉 Raw Body:\n{raw_body_str}", flush=True)

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

        print(f"👉 Trích xuất -> ID: {hop_dong_id} | File URL: {file_url}", flush=True)

        # Chạy trực tiếp tuần tự để bắt buộc in log ra màn hình Render ngay lập tức
        await process_signing_task(file_url, hop_dong_id, webhook_url)

        return JSONResponse(
            status_code=200,
            content={
                "message": "Đã xử lý xong yêu cầu ký file.",
                "hopDongThietKeId": hop_dong_id,
                "hopDongThietKeld": hop_dong_id,
                "status": "COMPLETED"
            }
        )
    except Exception as e:
        print(f"❌ [LỖI API SIGN]: {e}", flush=True)
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

with gr.Blocks(title="Twenty CRM Signing API") as demo:
    gr.Markdown("# 🖋️ Twenty CRM PDF Signing Backend")

app = gr.mount_gradio_app(app, demo, path="/")
