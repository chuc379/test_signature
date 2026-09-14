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

TWENTY_CRM_WEBHOOK_URL = "https://tinasoft.tinacrm.tinasoft.io/webhooks/workflows/YOUR_WORKFLOW_ID"
MY_PUBLIC_DOMAIN = "https://your-hf-space-name.hf.space"  # Đổi thành URL Space thật của bạn

# --- LOGIC XỬ LÝ KÝ DUYỆT PDF THỰC TẾ ---
def apply_digital_signature_to_pdf(input_pdf_bytes: bytes, output_path: str, signer_name: str) -> bool:
    """Đọc file PDF, đóng dấu 'ĐÃ DUYỆT' + tên người ký vào trang cuối cùng."""
    try:
        # 1. Mở file PDF từ bộ nhớ
        doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
        
        # 2. Chọn trang cuối cùng để đóng dấu
        page = doc[-1]
        
        # 3. Định vị tọa độ đóng dấu (Góc dưới bên phải trang)
        rect = page.rect
        x1, y1 = rect.width - 220, rect.height - 120
        x2, y2 = rect.width - 20, rect.height - 20
        sign_rect = fitz.Rect(x1, y1, x2, y2)

        # 4. Vẽ khung hình chữ nhật màu xanh/đỏ giả lập con dấu
        shape = page.new_shape()
        shape.draw_rect(sign_rect)
        shape.finish(color=(0, 0.5, 0), fill=(0.9, 1, 0.9), width=2) # Khung viền xanh lá
        shape.commit()

        # 5. Chèn nội dung văn bản ký duyệt vào trong khung
        sign_text = f"ĐÃ TRUY CẬP KÝ DUYỆT\nNgười ký: {signer_name}\nNgày ký: 2026-09-14\nTrạng thái: APPROVED"
        page.insert_textbox(
            sign_rect, 
            sign_text, 
            fontsize=9, 
            color=(0, 0.4, 0), 
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
            print(f"--> [WEBHOOK] Gửi về CRM thành công! Status: {res.status_code}")
        except Exception as e:
            print(f"--> [WEBHOOK ERROR] Không thể gửi tới CRM: {e}")


# --- API ENDPOINT (GỌI TỪ WF1) ---
@app.post("/api/v1/sign")
async def mock_sign_request(
    background_tasks: BackgroundTasks,
    hopDongThietKeId: str = Form("abcdef1111"),
    webhook_url: str = Form(default=TWENTY_CRM_WEBHOOK_URL),
    file: UploadFile = File(...) # Yêu cầu truyền file PDF lên
):
    # Đọc dữ liệu file upload
    pdf_content = await file.read()
    
    # Đưa logic xử lý ký file và gửi webhook vào background để API phản hồi ngay lập tức
    background_tasks.add_task(
        process_signing_task, 
        pdf_content, 
        hopDongThietKeId, 
        webhook_url
    )

    return JSONResponse(
        status_code=200,
        content={
            "message": "Đã tiếp nhận file. Hệ thống đang thực hiện đóng dấu ký duyệt.",
            "hopDongThietKeId": hopDongThietKeId,
            "status": "PROCESSING"
        }
    )