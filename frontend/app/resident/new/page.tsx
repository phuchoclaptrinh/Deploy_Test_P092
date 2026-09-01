"use client";

import { Camera, CircleAlert, ImagePlus, MapPin, Plus, RotateCcw, Send, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createResidentTicket, listLocations, uploadImage } from "@/api/backend.api";
import { ResidentShell } from "@/components/resident/ResidentShell";
import { ResidentAlert, ResidentConfirmDialog } from "@/components/resident/ResidentUI";
import { formatTicketCode } from "@/lib/display";
import { buildingManagementPhone } from "@/lib/residentContact";
import { residentErrorMessage } from "@/lib/residentErrors";
import { useResidentProfile } from "@/lib/residentProfile";
import { cacheResidentTicketPrefetch } from "@/lib/residentTicketPrefetch";
import type { TicketImage } from "@/lib/types";
import type { LocationItem, ResidentTicket } from "@/types/api";

/** R-02 Report an issue — a single full-height sheet, not a wizard. */
const MAX_PHOTOS = 5;
const MAX_PHOTO_BYTES = 10 * 1024 * 1024;
const MAX_DESCRIPTION = 5000;
const DESCRIPTION_COUNTER_FROM = 4500;
const ACCEPTED_TYPES = ["image/jpeg", "image/png", "image/webp"];

type DraftPhoto = {
  key: string;
  name: string;
  dataUrl: string;
  status: "preparing" | "uploading" | "ready" | "failed";
  uploadId?: string;
  error?: string;
  file?: File;
};

export default function ResidentReportPage() {
  const router = useRouter();
  const { user } = useResidentProfile();
  const [catalog, setCatalog] = useState<LocationItem[]>([]);
  const [catalogState, setCatalogState] = useState<"loading" | "ready" | "error">("loading");
  const [floor, setFloor] = useState("");
  const [area, setArea] = useState<"private" | "common" | "">("");
  const [unitCode, setUnitCode] = useState("");
  const [locationId, setLocationId] = useState("");
  const [description, setDescription] = useState("");
  const [photos, setPhotos] = useState<DraftPhoto[]>([]);
  const [photoError, setPhotoError] = useState("");
  const [attempted, setAttempted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const photoKeySeed = useRef(0);

  const loadCatalog = useCallback(() => {
    setCatalogState("loading");
    listLocations()
      .then((items) => { setCatalog(items); setCatalogState("ready"); })
      .catch(() => setCatalogState("error"));
  }, []);
  useEffect(loadCatalog, [loadCatalog]);

  const floors = useMemo(() => {
    const seen = new Set<string>();
    return catalog.filter((item) => {
      if (seen.has(item.floor_code)) return false;
      seen.add(item.floor_code);
      return true;
    });
  }, [catalog]);
  const locations = useMemo(() => catalog.filter((item) => item.floor_code === floor), [catalog, floor]);
  const units = useMemo(() => [...new Set(locations.map((item) => item.unit_code).filter((item): item is string => Boolean(item)))], [locations]);
  const apartmentLocations = useMemo(() => locations.filter((item) => item.unit_code === unitCode), [locations, unitCode]);
  const commonLocations = useMemo(() => locations.filter((item) => item.unit_code === null), [locations]);

  const dirty = Boolean(locationId || floor || area || unitCode || description.trim() || photos.length);
  const busyPhotos = photos.some((photo) => photo.status === "preparing" || photo.status === "uploading");
  const readyPhotos = photos.filter((photo) => photo.status === "ready");
  const canSubmit = Boolean(locationId) && description.trim().length > 0 && !busyPhotos && !submitting;

  const close = () => { if (dirty) setConfirmDiscard(true); else leave(); };
  const leave = () => { if (window.history.length > 1) router.back(); else router.replace("/resident"); };

  const startUpload = useCallback(async (photo: DraftPhoto, file: File) => {
    setPhotos((current) => current.map((item) => item.key === photo.key ? { ...item, status: "uploading", error: undefined } : item));
    try {
      const uploadId = await uploadImage({ name: file.name, dataUrl: photo.dataUrl, size: file.size });
      setPhotos((current) => current.map((item) => item.key === photo.key ? { ...item, status: "ready", uploadId } : item));
    } catch (reason) {
      const message = residentErrorMessage(reason, "Không tải được ảnh.");
      setPhotos((current) => current.map((item) => item.key === photo.key ? { ...item, status: "failed", error: message } : item));
    }
  }, []);

  const addPhotos = async (event: ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(event.target.files || []);
    event.target.value = "";
    if (!selected.length) return;
    setPhotoError("");
    if (photos.length >= MAX_PHOTOS) { setPhotoError(`Bạn đã thêm tối đa ${MAX_PHOTOS} ảnh.`); return; }
    const accepted: File[] = [];
    for (const file of selected.slice(0, MAX_PHOTOS - photos.length)) {
      if (!ACCEPTED_TYPES.includes(file.type)) { setPhotoError("Định dạng ảnh không được hỗ trợ. Hãy dùng JPEG, PNG hoặc WebP."); continue; }
      if (file.size > MAX_PHOTO_BYTES) { setPhotoError("Ảnh vượt quá dung lượng cho phép (tối đa 10 MB)."); continue; }
      accepted.push(file);
    }
    if (photos.length + selected.length > MAX_PHOTOS) setPhotoError(`Bạn chỉ có thể thêm tối đa ${MAX_PHOTOS} ảnh.`);
    for (const file of accepted) {
      const key = `photo-${photoKeySeed.current++}`;
      const entry: DraftPhoto = { key, name: file.name, dataUrl: "", status: "preparing", file };
      setPhotos((current) => [...current, entry]);
      try {
        const dataUrl = await readFileAsDataUrl(file);
        const prepared = { ...entry, dataUrl };
        setPhotos((current) => current.map((item) => item.key === key ? prepared : item));
        void startUpload(prepared, file);
      } catch {
        setPhotos((current) => current.map((item) => item.key === key ? { ...item, status: "failed", error: "Không đọc được ảnh này." } : item));
      }
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setAttempted(true);
    setError("");
    if (!canSubmit) return;
    setSubmitting(true);
    const finalDescription = description.trim();
    try {
      const result = await createResidentTicket(locationId, finalDescription, readyPhotos.map((photo) => photo.uploadId!));
      const now = new Date().toISOString();
      const optimistic: ResidentTicket = {
        id: result.ticket_id,
        display_code: formatTicketCode(result.ticket_id),
        description: finalDescription,
        display_status: result.display_status,
        category_display_name: null,
        priority_description: null,
        progress_text: "Đang phân tích phản ánh...",
        // §4: no start time until a technician is assigned, and never a
        // completion estimate. The wording above stands in and promises nothing.
        expected_start_at: null,
        location_label: locations.find((item) => item.id === locationId)?.label || "Chưa cập nhật vị trí",
        reporter_name: user?.full_name || null,
        is_reporter: true,
        lifecycle_group: "ACTIVE",
        invalid_reason_text: null,
        created_at: now,
        updated_at: now,
        available_actions: [],
        attachments: [],
        timeline: [{ display_status: result.display_status, reason: "Resident created ticket.", created_at: now }],
      };
      const draftImages: TicketImage[] = readyPhotos.map((photo) => ({ name: photo.name, dataUrl: photo.dataUrl }));
      cacheResidentTicketPrefetch(result.ticket_id, { ticket: optimistic, images: draftImages });
      // The draft is finished; the new report opens in its Checking state.
      router.replace(`/resident/tickets/${result.ticket_id}?created=1`);
    } catch (reason) {
      // The draft stays exactly as it is so the resident can retry.
      setError(residentErrorMessage(reason, "Không gửi được phản ánh. Vui lòng thử lại."));
      setSubmitting(false);
    }
  };

  const locationMissing = attempted && !locationId;
  const descriptionMissing = attempted && !description.trim();

  return (
    <ResidentShell title="Gửi phản ánh" variant="sheet" onClose={close} requireApartment>
      <form className="rdSheetForm" onSubmit={submit} noValidate>
        <section className="rdCard rdGuideCard" aria-labelledby="rd-report-guide">
          <strong id="rd-report-guide">Để xử lý nhanh hơn:</strong>
          <ul className="rdGuideList">
            <li><MapPin size={16} aria-hidden="true" />Chọn vị trí và mô tả ngắn sự cố</li>
            <li><Camera size={16} aria-hidden="true" />Thêm ảnh nếu có</li>
          </ul>
        </section>

        {error && <ResidentAlert tone="error">{error}</ResidentAlert>}

        <section className="rdFormGroup">
          <span className="rdFieldLabel">Sự cố xảy ra ở đâu?</span>
          {catalogState === "error" ? (
            <ResidentAlert tone="error">
              Không tải được danh sách vị trí.
              <button className="rdTextButton" type="button" onClick={loadCatalog}><RotateCcw size={15} />Thử lại</button>
            </ResidentAlert>
          ) : catalog.length === 0 && catalogState === "ready" ? (
            <ResidentAlert tone="warning">Chưa có danh sách vị trí. Vui lòng liên hệ Ban quản lý.</ResidentAlert>
          ) : (
            <>
              <div className="rdLocationGrid">
                <div className="rdField" data-invalid={locationMissing}>
                  <label htmlFor="rd-floor">Tầng</label>
                  <select id="rd-floor" value={floor} disabled={catalogState === "loading"} onChange={(event) => { setFloor(event.target.value); setArea(""); setUnitCode(""); setLocationId(""); }}>
                    <option value="">{catalogState === "loading" ? "Đang tải vị trí…" : "Chọn tầng"}</option>
                    {floors.map((item) => <option value={item.floor_code} key={item.floor_code}>{item.floor_display_name}</option>)}
                  </select>
                </div>
                <div className="rdField" data-invalid={locationMissing}>
                  <label htmlFor="rd-area">Loại khu vực</label>
                  <select id="rd-area" value={area} disabled={!floor} onChange={(event) => { setArea(event.target.value as "private" | "common" | ""); setUnitCode(""); setLocationId(""); }}>
                    <option value="">Chọn loại khu vực</option>
                    <option value="private">Trong căn hộ</option>
                    <option value="common">Khu vực chung</option>
                  </select>
                </div>
              </div>
              {area === "private" && <div className="rdLocationGrid">
                <div className="rdField" data-invalid={locationMissing}>
                  <label htmlFor="rd-unit">Căn hộ</label>
                  <select id="rd-unit" value={unitCode} onChange={(event) => { setUnitCode(event.target.value); setLocationId(""); }}>
                    <option value="">Chọn căn hộ</option>
                    {units.map((code) => <option value={code} key={code}>{code}</option>)}
                  </select>
                </div>
                <div className="rdField" data-invalid={locationMissing}>
                  <label htmlFor="rd-apartment-location">Vị trí</label>
                  <select id="rd-apartment-location" value={locationId} disabled={!unitCode} onChange={(event) => setLocationId(event.target.value)}>
                    <option value="">Chọn vị trí</option>
                    {apartmentLocations.map((item) => <option value={item.id} key={item.id}>{item.location_type_name}</option>)}
                  </select>
                </div>
              </div>}
              {area === "common" && <div className="rdField" data-invalid={locationMissing}>
                <label htmlFor="rd-common-location">Vị trí</label>
                <select id="rd-common-location" value={locationId} onChange={(event) => setLocationId(event.target.value)}>
                  <option value="">Chọn vị trí</option>
                  {commonLocations.map((item) => <option value={item.id} key={item.id}>{item.location_type_name}</option>)}
                </select>
              </div>}
              {locationMissing && <p className="rdFieldError"><CircleAlert size={15} />Vui lòng chọn vị trí xảy ra sự cố.</p>}
              <p className="rdFieldHint">
                <span>Không tìm thấy vị trí phù hợp? Hãy liên hệ Ban quản lý.</span>
                {buildingManagementPhone && <a className="rdTextButton" href={`tel:${buildingManagementPhone}`}>Gọi</a>}
              </p>
            </>
          )}
        </section>

        <section className="rdField" data-invalid={descriptionMissing}>
          <label htmlFor="rd-description">Mô tả sự cố</label>
          <textarea
            id="rd-description"
            value={description}
            maxLength={MAX_DESCRIPTION}
            onChange={(event) => setDescription(event.target.value.slice(0, MAX_DESCRIPTION))}
            placeholder="Điều gì đang xảy ra? Sự cố nằm chính xác ở đâu và bắt đầu từ khi nào?"
          />
          {descriptionMissing && <p className="rdFieldError"><CircleAlert size={15} />Vui lòng mô tả sự cố trước khi gửi.</p>}
          {description.length >= DESCRIPTION_COUNTER_FROM && <p className="rdFieldHint"><span /><span>Còn {MAX_DESCRIPTION - description.length} ký tự</span></p>}
        </section>

        <section className="rdFormGroup">
          <span className="rdFieldLabel">Ảnh (không bắt buộc)</span>
          {photos.length === 0 ? (
            <label className="rdUploadEmpty">
              <Plus size={26} aria-hidden="true" />
              <strong>Thêm ảnh</strong>
              <small>JPEG, PNG hoặc WebP · tối đa 5 ảnh</small>
              <input type="file" accept={ACCEPTED_TYPES.join(",")} multiple onChange={addPhotos} aria-label="Thêm ảnh" />
            </label>
          ) : (
            <div className="rdPhotoGrid">
              {photos.map((photo, index) => (
                <figure className="rdPhoto" key={photo.key}>
                  {photo.dataUrl && <img src={photo.dataUrl} alt={`Ảnh ${index + 1}`} />}
                  {photo.status === "ready" ? (
                    <button className="rdPhotoRemove" type="button" onClick={() => setPhotos((current) => current.filter((item) => item.key !== photo.key))} aria-label={`Xóa ảnh ${index + 1}`} title="Xóa ảnh"><X size={16} /></button>
                  ) : photo.status === "failed" ? (
                    <span className="rdPhotoState failed">
                      <span>{photo.error || "Không tải được ảnh."}</span>
                      <span className="rdPhotoActions">
                        <button type="button" onClick={() => photo.file && startUpload(photo, photo.file)}>Thử lại</button>
                        <button type="button" onClick={() => setPhotos((current) => current.filter((item) => item.key !== photo.key))}>Xóa</button>
                      </span>
                    </span>
                  ) : (
                    <span className="rdPhotoState"><span className="rdSpinner" /><span>{photo.status === "preparing" ? "Đang chuẩn bị…" : "Đang tải ảnh…"}</span></span>
                  )}
                </figure>
              ))}
              {photos.length < MAX_PHOTOS && (
                <label className="rdPhotoAdd">
                  <ImagePlus size={20} aria-hidden="true" />
                  <span>Thêm ảnh khác</span>
                  <input type="file" accept={ACCEPTED_TYPES.join(",")} multiple onChange={addPhotos} aria-label="Thêm ảnh khác" />
                </label>
              )}
            </div>
          )}
          {photos.length >= MAX_PHOTOS && <p className="rdFieldHint">Bạn đã thêm tối đa {MAX_PHOTOS} ảnh.</p>}
          {photoError && <p className="rdFieldError"><CircleAlert size={15} />{photoError}</p>}
        </section>

        <div className="rdStickyAction">
          <button className="rdButton" type="submit" disabled={!canSubmit}>
            {submitting ? <><span className="rdSpinner" />Đang gửi…</> : <><Send size={18} />Gửi phản ánh</>}
          </button>
          {!canSubmit && !submitting && <small>{busyPhotos ? "Vui lòng đợi ảnh tải xong." : "Hãy chọn vị trí và mô tả sự cố để gửi."}</small>}
        </div>
      </form>

      {confirmDiscard && (
        <ResidentConfirmDialog
          title="Rời khỏi phản ánh này?"
          body="Những thay đổi chưa gửi sẽ bị mất."
          safeLabel="Tiếp tục chỉnh sửa"
          destructiveLabel="Bỏ bản nháp"
          onSafe={() => setConfirmDiscard(false)}
          onDestructive={leave}
        />
      )}
    </ResidentShell>
  );
}

function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("read-failed"));
    reader.readAsDataURL(file);
  });
}
