import { ApiError } from "@/api/client";

/** Resident-safe error-to-copy mapping. Residents never see a backend error code. */
const messages: Record<string, string> = {
  AUTH_REQUIRED: "Phiên đăng nhập đã hết hạn.",
  AUTH_TOKEN_INVALID: "Phiên đăng nhập đã hết hạn.",
  AUTH_TOKEN_EXPIRED: "Phiên đăng nhập đã hết hạn.",
  USER_INACTIVE: "Tài khoản đang tạm khóa. Vui lòng liên hệ Ban quản lý.",
  NO_ACTIVE_UNIT: "Tài khoản chưa được liên kết với căn hộ.",
  INVALID_LOCATION: "Vị trí này không còn khả dụng. Vui lòng chọn lại.",
  INVALID_ATTACHMENT: "Không thể sử dụng ảnh này.",
  ATTACHMENT_NOT_FOUND: "Không thể sử dụng ảnh này.",
  IMAGE_UNREADABLE: "Không đọc được ảnh này. Vui lòng chọn ảnh khác.",
  DESCRIPTION_REQUIRED: "Vui lòng mô tả sự cố trước khi gửi.",
  TICKET_NOT_FOUND: "Không tìm thấy phản ánh này.",
  TICKET_NOT_OWNED: "Bạn không có quyền thực hiện thao tác này.",
  FORBIDDEN: "Bạn không có quyền thực hiện thao tác này.",
  INVALID_STATUS_TRANSITION: "Trạng thái phản ánh vừa được cập nhật.",
  INFORMATION_REQUEST_NOT_FOUND: "Yêu cầu bổ sung này không còn hiệu lực.",
  NETWORK_ERROR: "Không có kết nối. Vui lòng kiểm tra mạng và thử lại.",
  TIMEOUT: "Kết nối chậm nên chưa gửi được. Vui lòng thử lại.",
  UPLOAD_FAILED: "Không tải được ảnh.",
};

/** The backend already returns resident-safe Vietnamese text for these. */
const passThroughCodes = new Set(["TICKET_CREATE_RATE_LIMITED"]);

export function residentErrorMessage(reason: unknown, fallback = "Đã xảy ra lỗi. Vui lòng thử lại.") {
  if (reason instanceof ApiError) {
    if (passThroughCodes.has(reason.code)) return reason.message;
    return messages[reason.code] || fallback;
  }
  return fallback;
}

export function isRateLimited(reason: unknown) {
  return reason instanceof ApiError && reason.code === "TICKET_CREATE_RATE_LIMITED";
}

export function isSessionExpired(reason: unknown) {
  return reason instanceof ApiError && ["AUTH_REQUIRED", "AUTH_TOKEN_INVALID", "AUTH_TOKEN_EXPIRED"].includes(reason.code);
}
