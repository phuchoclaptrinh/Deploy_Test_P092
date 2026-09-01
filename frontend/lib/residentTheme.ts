/** The Resident app ships two complete looks. Both are white-based; only the
 *  accent differs, and it is applied to every accented surface at once so a
 *  screen is never half one theme and half the other.
 *
 *  The value lives on <html> as `data-rd-theme`, written by an inline script in
 *  the Resident layout before first paint. React never owns that attribute, so
 *  switching cannot cause a hydration mismatch or a flash of the other look. */
export type ResidentTheme = "blue" | "orange";

export const residentThemeKey = "fixit-resident-theme";

export const residentThemes: Array<{ value: ResidentTheme; label: string; hint: string; accent: string }> = [
  { value: "blue", label: "Xanh dương", hint: "Giống giao diện Ban quản lý", accent: "#2F6FED" },
  { value: "orange", label: "Cam", hint: "Tông ấm, nổi bật nút gửi phản ánh", accent: "#D94801" },
];

/** Browser-chrome colour, taken from the same accents the swatches preview. */
const themeColor = Object.fromEntries(residentThemes.map((item) => [item.value, item.accent])) as Record<ResidentTheme, string>;

/** The script the layout inlines. Kept here so the key, the attribute and the
 *  colours it writes cannot drift from the ones this module reads. */
export const residentThemeBootstrap = [
  "try{",
  `var t=localStorage.getItem(${JSON.stringify(residentThemeKey)})==="orange"?"orange":"blue";`,
  "document.documentElement.dataset.rdTheme=t;",
  `var m=document.querySelector('meta[name="theme-color"]');`,
  `if(m)m.setAttribute("content",t==="orange"?${JSON.stringify(themeColor.orange)}:${JSON.stringify(themeColor.blue)});`,
  '}catch(e){document.documentElement.dataset.rdTheme="blue";}',
].join("");

export function readResidentTheme(): ResidentTheme {
  if (typeof document === "undefined") return "blue";
  return document.documentElement.dataset.rdTheme === "orange" ? "orange" : "blue";
}

export function setResidentTheme(theme: ResidentTheme) {
  document.documentElement.dataset.rdTheme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", themeColor[theme]);
  try {
    window.localStorage.setItem(residentThemeKey, theme);
  } catch {
    // A browser blocking storage still gets the theme for this session.
  }
}
