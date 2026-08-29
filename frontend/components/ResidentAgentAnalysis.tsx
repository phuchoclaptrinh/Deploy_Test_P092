"use client";

import { Camera, CircleAlert, RotateCcw, Send, Timer } from "lucide-react";
import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { answerResidentAgentQuestion, getResidentAgentQuestion, getResidentTicket, listLocations } from "@/api/backend.api";
import { ResidentAlert } from "@/components/resident/ResidentUI";
import { residentErrorMessage } from "@/lib/residentErrors";
import { isCheckingReport } from "@/lib/residentStatus";
import type { TicketImage } from "@/lib/types";
import type { LocationItem, ResidentAgentQuestion, ResidentTicket } from "@/types/api";

/** R-04A Checking and R-04B Follow-up question.
 *  States follow docs/ui/COMPONENT_STATES.md section 15. */
type ResidentAgentAnalysisProps = {
  ticketId?: string;
  onComplete: (ticket: ResidentTicket) => void | Promise<void>;
  /** The detail screen keeps polling silently while no resident input is needed. */
  hideCheckingState?: boolean;
  /** Lets the report header show "Đang chờ bạn trả lời" while a question is open. */
  onQuestionChange?: (open: boolean) => void;
};

const MAX_ROUNDS = 3;
const MAX_ANSWER_LENGTH = 2000;
const LEAVE_HINT_AFTER_MS = 20_000;
const ACCEPTED_TYPES = ["image/jpeg", "image/png", "image/webp"];
const MAX_PHOTO_BYTES = 10 * 1024 * 1024;

const isOtherOption = (option: string) => /^khác\b/i.test(option.trim()) || /vui lòng ghi rõ/i.test(option);
const isPhotoOption = (option: string) => /(chụp|gửi|tải).{0,20}ảnh/i.test(option);

export function ResidentAgentAnalysis({ ticketId, onComplete, onQuestionChange, hideCheckingState = false }: ResidentAgentAnalysisProps) {
  const [question, setQuestion] = useState<ResidentAgentQuestion | null>(null);
  const [selectedOption, setSelectedOption] = useState("");
  const [freeText, setFreeText] = useState("");
  const [freeTextMode, setFreeTextMode] = useState(false);
  const [image, setImage] = useState<TicketImage>();
  /** Only ever set from the fixed selector below, never parsed out of the text
   *  answer: the backend validates this id and moves the report to it. */
  const [locationId, setLocationId] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [answered, setAnswered] = useState(false);
  const [showLeaveHint, setShowLeaveHint] = useState(false);
  const questionId = useRef<string | null>(null);
  const submittingRef = useRef(false);
  const refreshingRef = useRef(false);
  const refreshVersion = useRef(0);
  const completing = useRef(false);
  const onCompleteRef = useRef(onComplete);

  useEffect(() => { onCompleteRef.current = onComplete; }, [onComplete]);
  useEffect(() => { onQuestionChange?.(question !== null); }, [onQuestionChange, question]);
  useEffect(() => {
    const timer = window.setTimeout(() => setShowLeaveHint(true), LEAVE_HINT_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, []);

  const resetAnswer = useCallback(() => {
    setSelectedOption("");
    setFreeText("");
    setFreeTextMode(false);
    setImage(undefined);
    setLocationId("");
  }, []);

  const refresh = useCallback(async () => {
    if (!ticketId || submittingRef.current || completing.current || refreshingRef.current) return;
    const version = refreshVersion.current;
    refreshingRef.current = true;
    try {
      const [ticket, nextQuestion] = await Promise.all([
        getResidentTicket(ticketId, true),
        getResidentAgentQuestion(ticketId, true),
      ]);
      if (version !== refreshVersion.current || submittingRef.current) return;
      if (!isCheckingReport(ticket)) {
        completing.current = true;
        await onCompleteRef.current(ticket);
        return;
      }
      if (questionId.current !== (nextQuestion?.id || null)) {
        questionId.current = nextQuestion?.id || null;
        resetAnswer();
        if (nextQuestion) setAnswered(false);
      }
      setQuestion(nextQuestion);
      setError("");
    } catch (reason) {
      if (version !== refreshVersion.current || submittingRef.current) return;
      completing.current = false;
      setError(residentErrorMessage(reason, "Không cập nhật được kết quả kiểm tra."));
    } finally {
      refreshingRef.current = false;
    }
  }, [resetAnswer, ticketId]);

  useEffect(() => {
    completing.current = false;
    if (!ticketId) return;
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 3_000);
    return () => window.clearInterval(timer);
  }, [refresh, ticketId]);

  const remaining = useCountdown(question?.expires_at);
  const expired = question !== null && remaining !== null && remaining <= 0;

  const readImage = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!ACCEPTED_TYPES.includes(file.type)) { setError("Định dạng ảnh không được hỗ trợ."); return; }
    if (file.size > MAX_PHOTO_BYTES) { setError("Ảnh vượt quá dung lượng cho phép."); return; }
    const reader = new FileReader();
    reader.onload = () => {
      setImage({ name: file.name, dataUrl: String(reader.result), size: file.size });
      setSelectedOption("");
      setFreeText("");
      setFreeTextMode(false);
      setError("");
    };
    reader.onerror = () => setError("Không đọc được ảnh này.");
    reader.readAsDataURL(file);
  };

  const submitAnswer = async (event: FormEvent) => {
    event.preventDefault();
    if (!ticketId || !question || (!selectedOption && !freeText.trim() && !image)) return;
    // Picking a different location is only an answer once a location is
    // actually picked; sending the option alone would tell the backend nothing.
    if (needsNewLocation(question, selectedOption) && !locationId) return;
    const answeredQuestion = question;
    refreshVersion.current += 1;
    submittingRef.current = true;
    setSubmitting(true);
    setQuestion(null);
    setError("");
    try {
      await answerResidentAgentQuestion(ticketId, answeredQuestion.id, {
        option: selectedOption || undefined,
        text: freeText.trim() || undefined,
        image,
        // Only the "somewhere else" answer carries a location. The backend
        // rejects a replacement id sent alongside "keep it", so sending the
        // leftover state here would turn a valid answer into a 400.
        locationId: needsNewLocation(answeredQuestion, selectedOption) ? locationId : undefined,
      });
      questionId.current = null;
      resetAnswer();
      setAnswered(true);
    } catch (reason) {
      // Keep the answer so the resident can send it again.
      questionId.current = answeredQuestion.id;
      setQuestion(answeredQuestion);
      setError(residentErrorMessage(reason, "Không gửi được câu trả lời. Vui lòng thử lại."));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
      window.setTimeout(() => { void refresh(); }, 600);
    }
  };

  if (!question) {
    if (hideCheckingState) return null;
    return <CheckingState submitting={submitting} answered={answered} showLeaveHint={showLeaveHint} error={error} onRetry={() => { setError(""); void refresh(); }} />;
  }

  const options = question.options || [];
  const photoOption = options.find(isPhotoOption);
  const textOptions = options.filter((option) => !isPhotoOption(option));
  const canUseFreeText = question.question_type === "FREE_TEXT" || question.allow_free_text_fallback;
  const showTextArea = question.question_type === "FREE_TEXT" || freeTextMode || (canUseFreeText && textOptions.length === 0);
  const locationQuestion = question.question_kind === "LOCATION_CONFIRMATION";
  const pickingLocation = needsNewLocation(question, selectedOption);
  const canSend = Boolean(selectedOption || freeText.trim() || image) && !submitting && (!pickingLocation || Boolean(locationId));

  return (
    <section className="rdQuestion" aria-labelledby="rd-question-title">
      <div className="rdQuestionTop">
        <strong id="rd-question-title">Cần bạn cung cấp thêm thông tin</strong>
        <span className="rdQuestionRound">Câu hỏi {Math.min(question.round_number, MAX_ROUNDS)}/{MAX_ROUNDS}</span>
      </div>
      {remaining !== null && <Countdown remaining={remaining} />}

      {expired ? (
        <ResidentAlert tone="warning">Đã hết thời gian trả lời.</ResidentAlert>
      ) : (
        <form className="rdFormGroup" onSubmit={submitAnswer}>
          <p className="rdQuestionText">{question.question_text}</p>

          {textOptions.length > 0 && (
            <div className="rdChoices" role="group" aria-label="Lựa chọn trả lời">
              {textOptions.map((option) => {
                const other = isOtherOption(option) && canUseFreeText;
                const pressed = other ? freeTextMode : selectedOption === option;
                return (
                  <button
                    className="rdChoice"
                    type="button"
                    key={option}
                    aria-pressed={pressed}
                    onClick={() => {
                      if (other) { setFreeTextMode(true); setSelectedOption(""); }
                      else { setSelectedOption(option); setFreeTextMode(false); setFreeText(""); }
                      setImage(undefined);
                      setLocationId("");
                    }}
                  >{option}</button>
                );
              })}
            </div>
          )}

          {showTextArea && (
            <div className="rdField">
              <label htmlFor="rd-answer">Câu trả lời của bạn</label>
              <textarea
                id="rd-answer"
                autoFocus={question.question_type === "FREE_TEXT"}
                value={freeText}
                maxLength={MAX_ANSWER_LENGTH}
                onChange={(event) => { setFreeText(event.target.value); setSelectedOption(""); setImage(undefined); }}
                placeholder="Nhập câu trả lời…"
              />
            </div>
          )}

          {pickingLocation && <LocationPicker value={locationId} onChange={setLocationId} />}

          {/* A location confirmation is answered from the fixed selector, so the
              generic photo answer would only be a way to sidestep it. */}
          {!locationQuestion && <label className={`rdPhotoAnswer${image ? " selected" : ""}`}>
            <Camera size={17} aria-hidden="true" />
            {image?.name || photoOption || "Chụp ảnh mới"}
            <input type="file" accept={ACCEPTED_TYPES.join(",")} onChange={readImage} aria-label="Chụp ảnh mới" />
          </label>}

          {error && <ResidentAlert tone="error">{error}</ResidentAlert>}

          <button className="rdButton" type="submit" disabled={!canSend}>
            {submitting ? <><span className="rdSpinner" />Đang gửi câu trả lời…</> : <><Send size={18} />Gửi câu trả lời</>}
          </button>
          <small className="rdHelperText">Hệ thống sẽ tiếp tục kiểm tra ngay sau khi nhận câu trả lời.</small>
        </form>
      )}
    </section>
  );
}

/** True when the resident chose "somewhere else" on a location confirmation. */
function needsNewLocation(question: ResidentAgentQuestion, selectedOption: string) {
  if (question.question_kind !== "LOCATION_CONFIRMATION") return false;
  const options = question.options || [];
  // The "keep it" answer is the first option the backend offers; anything else
  // on a location question means the resident wants to move the report.
  return Boolean(selectedOption) && selectedOption !== options[0];
}

/** The same fixed selector a report is created with: floor, then area type,
 *  then the apartment when the area is a private one, then the location.
 *
 *  Deliberately the identical shape rather than a free-text box or a flat list:
 *  the resident is re-picking from the catalog, and the answer that leaves here
 *  is a `location_id` the backend can validate. */
function LocationPicker({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const [catalog, setCatalog] = useState<LocationItem[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [floor, setFloor] = useState("");
  const [area, setArea] = useState<"private" | "common" | "">("");
  const [unitCode, setUnitCode] = useState("");

  useEffect(() => {
    let active = true;
    listLocations()
      .then((rows) => { if (active) { setCatalog(rows); setState("ready"); } })
      .catch(() => { if (active) setState("error"); });
    return () => { active = false; };
  }, []);

  const floors = useMemo(() => {
    const seen = new Set<string>();
    return catalog.filter((item) => {
      if (seen.has(item.floor_code)) return false;
      seen.add(item.floor_code);
      return true;
    });
  }, [catalog]);
  const onFloor = useMemo(() => catalog.filter((item) => item.floor_code === floor), [catalog, floor]);
  const units = useMemo(() => [...new Set(onFloor.map((item) => item.unit_code).filter((item): item is string => Boolean(item)))], [onFloor]);
  const apartmentLocations = useMemo(() => onFloor.filter((item) => item.unit_code === unitCode), [onFloor, unitCode]);
  const commonLocations = useMemo(() => onFloor.filter((item) => item.unit_code === null), [onFloor]);

  if (state === "error") return <ResidentAlert tone="error">Không tải được danh sách vị trí. Vui lòng thử lại.</ResidentAlert>;

  return <div className="rdLocationPicker">
    <div className="rdField">
      <label htmlFor="rd-answer-floor">Tầng</label>
      <select id="rd-answer-floor" value={floor} disabled={state === "loading"} onChange={(event) => { setFloor(event.target.value); setArea(""); setUnitCode(""); onChange(""); }}>
        <option value="">Chọn tầng</option>
        {floors.map((item) => <option value={item.floor_code} key={item.floor_code}>{item.floor_display_name}</option>)}
      </select>
    </div>
    <div className="rdField">
      <label htmlFor="rd-answer-area">Khu vực</label>
      <select id="rd-answer-area" value={area} disabled={!floor} onChange={(event) => { setArea(event.target.value as "private" | "common" | ""); setUnitCode(""); onChange(""); }}>
        <option value="">Chọn khu vực</option>
        <option value="private">Trong căn hộ</option>
        <option value="common">Khu vực chung</option>
      </select>
    </div>
    {area === "private" && <div className="rdField">
      <label htmlFor="rd-answer-unit">Căn hộ</label>
      <select id="rd-answer-unit" value={unitCode} onChange={(event) => { setUnitCode(event.target.value); onChange(""); }}>
        <option value="">Chọn căn hộ</option>
        {units.map((item) => <option value={item} key={item}>{item}</option>)}
      </select>
    </div>}
    {area === "private" && <div className="rdField">
      <label htmlFor="rd-answer-apartment-location">Vị trí</label>
      <select id="rd-answer-apartment-location" value={value} disabled={!unitCode} onChange={(event) => onChange(event.target.value)}>
        <option value="">Chọn vị trí</option>
        {apartmentLocations.map((item) => <option value={item.id} key={item.id}>{item.location_type_name}</option>)}
      </select>
    </div>}
    {area === "common" && <div className="rdField">
      <label htmlFor="rd-answer-common-location">Vị trí</label>
      <select id="rd-answer-common-location" value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Chọn vị trí</option>
        {commonLocations.map((item) => <option value={item.id} key={item.id}>{item.location_type_name}</option>)}
      </select>
    </div>}
  </div>;
}

function CheckingState({ submitting, answered, showLeaveHint, error, onRetry }: { submitting: boolean; answered: boolean; showLeaveHint: boolean; error: string; onRetry: () => void }) {
  return (
    <section className="rdCard rdLoading" role="status" aria-live="polite">
      <span className="rdSpinner large" aria-hidden="true" />
      <strong>{submitting ? "Đang gửi câu trả lời…" : answered ? "Cảm ơn bạn. Hệ thống đang kiểm tra lại phản ánh." : "Đang kiểm tra phản ánh"}</strong>
      <p>{answered ? "Bạn sẽ thấy kết quả ngay khi hệ thống kiểm tra xong." : "Hệ thống đang đọc nội dung và hình ảnh bạn đã gửi."}</p>
      {showLeaveHint && !submitting && <p>Bạn có thể rời trang này và theo dõi tiến độ trong mục Phản ánh.</p>}
      {error && <ResidentAlert tone="error">{error}<button className="rdTextButton" type="button" onClick={onRetry}><RotateCcw size={15} />Thử lại</button></ResidentAlert>}
    </section>
  );
}

/** C-15 timer: derived from the backend expiry, announced only at thresholds. */
function Countdown({ remaining }: { remaining: number }) {
  const level = remaining <= 15 ? "urgent" : remaining <= 60 ? "warning" : "normal";
  const minutes = Math.max(0, Math.floor(remaining / 60));
  const seconds = Math.max(0, remaining % 60);
  return <>
    <span className="rdTimer" data-level={level} aria-hidden="true">
      {level === "normal" ? <Timer size={14} /> : <CircleAlert size={14} />}
      Còn {minutes}:{String(seconds).padStart(2, "0")} để trả lời
    </span>
    <span className="rdSrOnly" role="status" aria-live="polite">
      {level === "urgent" ? "Còn 15 giây để trả lời." : level === "warning" ? "Còn 1 phút để trả lời." : ""}
    </span>
  </>;
}

function useCountdown(expiresAt: string | null | undefined) {
  const [remaining, setRemaining] = useState<number | null>(null);
  useEffect(() => {
    if (!expiresAt) { setRemaining(null); return; }
    const deadline = new Date(expiresAt).getTime();
    if (Number.isNaN(deadline)) { setRemaining(null); return; }
    const tick = () => setRemaining(Math.max(0, Math.round((deadline - Date.now()) / 1000)));
    tick();
    const timer = window.setInterval(tick, 1_000);
    return () => window.clearInterval(timer);
  }, [expiresAt]);
  return remaining;
}
