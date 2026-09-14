import asyncio
import os
import fitz  # PyMuPDF
from fastapi import FastAPI, BackgroundTasks, Form, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx

app = FastAPI(title="Twenty CRM Real Signing Mock Server")

# Tạo thư mục chứa file đã ký và file chữ ký/con dấu mẫu
UPLOAD_DIR = "signed_files"
ASSETS_DIR = "assets"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)

# Cho phép truy cập file public để Twenty CRM có thể tải về
app.mount("/files", StaticFiles(directory=UPLOAD_DIR), name="files")

# --- TỰ ĐỘNG NHẬN DOMAIN TỪ HUGGING FACE SPACES ---
# Hugging Face tự động tạo sẵn biến SPACE_HOST (ví dụ: "username-spacename.hf.space")
SPACE_HOST = os.getenv("SPACE_HOST")
if SPACE_HOST:
    MY_PUBLIC_DOMAIN = f"https://{SPACE_HOST}"
else:
    MY_PUBLIC_DOMAIN = os.getenv("MY_PUBLIC_DOMAIN", "http://localhost:7860")

# URL Webhook CRM (Lấy từ biến môi trường trên HF Spaces)
TWENTY_CRM_WEBHOOK_URL = os.getenv(
    "TWENTY_CRM_WEBHOOK_URL", 
    "https://tinasoft.tinacrm.tinasoft.io/webhooks/workflows/YOUR_WORKFLOW_ID"
)

# Link ảnh con dấu public
STAMP_IMAGE_URL = "https://i.pinimg.com/736x/43/da/7d/43da7d45279d0f4c042a2bf2079f918b.jpg"
STAMP_LOCAL_PATH = os.path.join(ASSETS_DIR, "stamp.jpg")


# --- TỰ ĐỘNG TẢI CON DẤU KHI KHỞI ĐỘNG APP ---
async def download_stamp_image():
    """Tải ảnh con dấu từ URL public về lưu ở thư mục assets nếu chưa có."""
    if not os.path.exists(STAMP_LOCAL_PATH):
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(STAMP_IMAGE_URL, follow_redirects=True, timeout=15.0)
                if res.status_code == 200:
                    with open(STAMP_LOCAL_PATH, "wb") as f:
                        f.write(res.content)
                    print("--> [INIT] Đã tải ảnh con dấu thành công!")
                else:
                    print(f"--> [INIT ERROR] Không thể tải ảnh con dấu, Status: {res.status_code}")
        except Exception as e:
            print(f"--> [INIT ERROR] Lỗi tải ảnh con dấu: {e}")

@app.on_event("startup")
async def startup_event():
    await download_stamp_image()


# --- LOGIC XỬ LÝ KÝ DUYỆT PDF BẰNG MẪU CON DẤU TẢI VỀ ---
def apply_digital_signature_to_pdf(input_pdf_bytes: bytes, output_path: str, signer_name: str) -> bool:
    """Đọc file PDF, chèn ảnh con dấu từ link public vào trang cuối cùng."""
    try:
        # 1. Mở file PDF từ bộ nhớ
        doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
        
        # 2. Chọn trang cuối cùng để đóng dấu
        page = doc[-1]
        
        # 3. Định vị tọa độ đóng dấu (Góc dưới bên phải trang)
        rect = page.rect
        x1, y1 = rect.width - 200, rect.height - 140
        x2, y2 = rect.width - 20, rect.height - 20
        sign_rect = fitz.Rect(x1, y1, x2, y2)

        # 4. Chèn ảnh con dấu nếu đã được tải về
        if os.path.exists(STAMP_LOCAL_PATH):
            page.insert_image(sign_rect, filename=STAMP_LOCAL_PATH)
        else:
            # Fallback trường hợp không tải được ảnh: Vẽ khung hình chữ nhật giả lập
            shape = page.new_shape()
            shape.draw_rect(sign_rect)
            shape.finish(color=(0.8, 0, 0), fill=(1, 0.9, 0.9), width=2)
            shape.commit()

        # 5. Ghi thêm văn bản thông tin người ký đè nhẹ lên/dưới con dấu
        sign_text = f"ĐÃ TRUY CẬP KÝ DUYỆT\nNgười ký: {signer_name}\nNgày ký: 2026-09-14"
        page.insert_textbox(
            sign_rect, 
            sign_text, 
            fontsize=8, 
            color=(0.8, 0, 0), 
            align=fitz.TEXT_ALIGN_CENTER
        )

        # 6. Lưu file PDF mới ra đĩa
        doc.save(output_path)
        doc.close()
        return True
    except Exception as e:
        print(f"[PDF Error] Lỗi khi xử lý ký file: {e}")
        return False


# --- TASK CHẠY NGẦM ĐỂ XỬ LÝ KÝ VÀ BẮN WEBHOOK ---
async def process_signing_task(pdf_bytes: bytes, hop_dong_id: str, webhook_url: str):
    # Đảm bảo ảnh con dấu đã tải sẵn trước khi ký
    if not os.path.exists(STAMP_LOCAL_PATH):
        await download_stamp_image()

    output_filename = f"signed_{hop_dong_id}.pdf"
    output_filepath = os.path.join(UPLOAD_DIR, output_filename)
    
    # 1. Thực hiện logic ký duyệt PDF
    is_signed = apply_digital_signature_to_pdf(
        input_pdf_bytes=pdf_bytes, 
        output_path=output_filepath, 
        signer_name="Trưởng Phòng Bổ Sung"
    )
    
    signed_file_url = f"{MY_PUBLIC_DOMAIN}/files/{output_filename}" if is_signed else ""

    # 2. Tạo Payload gửi về Webhook WF2 của Twenty CRM
    payload = {
        "linkFile": signed_file_url,
        "isSuccess": is_signed,
        "hopDongThietKeId": hop_dong_id
    }
    
    # 3. Gửi thông báo kết quả cho Twenty CRM
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(webhook_url, json=payload, timeout=10.0)
            print(f"--> [WEBHOOK] Gửi về CRM thành công! Link: {signed_file_url} | Status: {res.status_code}")
        except Exception as e:
            print(f"--> [WEBHOOK ERROR] Không thể gửi tới CRM: {e}")


# --- API ENDPOINT (GỌI TỪ WF1) ---
@app.post("/api/v1/sign")
async def mock_sign_request(
    background_tasks: BackgroundTasks,
    hopDongThietKeId: str = Form("abcdef1111"),
    webhook_url: str = Form(default=None),
    file: UploadFile = File(...)
):
    pdf_content = await file.read()
    
    # Ưu tiên lấy webhook_url truyền lên từ Form, nếu không truyền sẽ lấy từ biến môi trường TWENTY_CRM_WEBHOOK_URL
    final_webhook_url = webhook_url if webhook_url else TWENTY_CRM_WEBHOOK_URL
    
    background_tasks.add_task(
        process_signing_task, 
        pdf_content, 
        hopDongThietKeId, 
        final_webhook_url
    )

    return JSONResponse(
        status_code=200,
        content={
            "message": "Đã tiếp nhận file. Hệ thống đang thực hiện đóng dấu ký duyệt.",
            "hopDongThietKeId": hopDongThietKeId,
            "status": "PROCESSING"
        }
    )