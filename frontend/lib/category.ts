const categoryLabels: Record<string, string> = {
  WATER: "Nước",
  WALL_DAMP: "Tường ẩm, thấm",
  WATER_LEAK: "Rò rỉ nước",
  ELECTRICAL_SHORT: "Chập điện",
  ELEVATOR: "Thang máy",
  POWER_OUTAGE: "Mất điện",
  SECURITY_SAFETY: "An ninh và an toàn",
  NOISE: "Tiếng ồn",
  SERIOUS_SECURITY_DISORDER: "An ninh nghiêm trọng",
  LOCK_DOOR: "Khóa và cửa",
  HVAC: "Điều hòa và thông gió",
  INTERNET_TV: "Internet và truyền hình",
  COMMON_AREA_DAMAGE: "Hư hỏng khu vực chung",
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
