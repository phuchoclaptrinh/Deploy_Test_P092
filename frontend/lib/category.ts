const categoryLabels: Record<string, string> = {
  WATER_LEAK: "Rò rỉ nước",
  ELECTRICAL_SHORT: "Chập điện",
  ELEVATOR: "Thang máy",
  SERIOUS_SECURITY_DISORDER: "An ninh nghiêm trọng",
  LOCK_DOOR: "Khóa và cửa",
  HVAC: "Điều hòa và thông gió",
  LOCAL_POWER_OUTAGE: "Mất điện cục bộ",
  STRUCTURAL_ISSUE: "Hư hỏng kết cấu",
  COMMON_LIGHT: "Chiếu sáng khu vực chung",
  ODOR_HYGIENE: "Mùi hôi và vệ sinh",
  NOISE_NEIGHBOR: "Tiếng ồn hàng xóm",
};

export function formatCategoryName(category: string | null | undefined, fallback?: string | null) {
  if (!category) return fallback || "Chưa phân loại";
  return categoryLabels[category] || fallback || category;
}
