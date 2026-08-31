"use client";

import {
  ArrowLeft,
  ArrowRight,
  BellRing,
  Camera,
  Check,
  ClipboardList,
  Clock3,
  Eye,
  EyeOff,
  Home,
  Mail,
  MapPin,
  MessageSquare,
  Plus,
  PhoneCall,
  Search,
  ShieldCheck,
  Sparkles,
  UserRound,
  Wrench,
} from "lucide-react";
import { CSSProperties, FormEvent, PointerEvent as ReactPointerEvent, ReactNode, useCallback, useEffect, useRef, useState } from "react";
import styles from "./CinematicLogin.module.css";

type CinematicLoginProps = {
  email: string;
  password: string;
  error: string;
  busy: boolean;
  showPassword: boolean;
  onEmailChange: (value: string) => void;
  onPasswordChange: (value: string) => void;
  onPasswordVisibilityChange: () => void;
  onSubmit: (event: FormEvent) => void;
};

const slides = [
  {
    eyebrow: "PHÂN VIỆC TỰ ĐỘNG",
    title: "Phân việc tự động với thời gian cam kết đưa ra",
    body: "Hệ thống tự chọn nhóm phụ trách và ấn định thời gian cam kết hoàn thành cho từng phản ánh.",
    kind: "assignment",
  },
  {
    eyebrow: "PHÂN LOẠI VÀ ĐỊNH TUYẾN",
    title: "Đúng vấn đề. Đúng bộ phận.",
    body: "Hệ thống hỗ trợ phân loại và chuyển phản ánh đến đúng nhóm phụ trách.",
    kind: "routing",
  },
  {
    eyebrow: "THEO DÕI MINH BẠCH",
    title: "Mỗi phản ánh được theo dõi đến khi hoàn tất.",
    body: "Cư dân luôn biết ai đang xử lý và thời điểm dự kiến hoàn thành.",
    kind: "expert",
  },
] as const;

const SLIDE_DURATION = 4000;
const FINAL_SLIDE_DURATION = 3000;
const PRE_PHASE = 190;
const RUN_PHASE = 1400;

const fixitSlides = [
  {
    eyebrow: "Tạo phản ánh",
    title: "Báo sự cố chỉ trong vài chạm.",
    body: "Chọn vị trí, mô tả vấn đề và thêm hình ảnh trực tiếp trên app. Thao tác đơn giản, thông tin đầy đủ, phản ánh được gửi đi ngay lập tức.",
    ticketTitle: "Rò nước hành lang",
    ticketStatus: "Đang phân tích",
    ticketNote: "Ảnh và vị trí đã gửi",
  },
  {
    eyebrow: "FixIt Agent",
    title: "Phản ánh được xử lý ngay khi gửi.",
    body: "FixIt Agent tự động tiếp nhận, phân tích và chuyển thông tin đến Ban quản lý trong tích tắc. Mọi phản ánh đều được xử lý nhanh và chính xác.",
    ticketTitle: "Đang đọc phản ánh",
    ticketStatus: "FixIt Agent",
    ticketNote: "Vị trí, mô tả và ảnh",
  },
  {
    eyebrow: "Theo dõi ticket",
    title: "Theo dõi tiến độ ngay trên app.",
    body: "Mọi cập nhật đều được hiển thị rõ ràng theo từng trạng thái. Từ lúc tiếp nhận đến khi xử lý hoàn tất, cư dân luôn nắm được tiến độ sự cố.",
    ticketTitle: "KTV Minh An",
    ticketStatus: "Đã có kỹ thuật viên",
    ticketNote: "Dự kiến bắt đầu 12:24",
  },
  {
    eyebrow: "Xử lý kịp thời",
    title: "Kỹ thuật viên tiếp nhận và xử lý nhanh chóng.",
    body: "Khi ticket được giao, kỹ thuật viên nhận đầy đủ thông tin sự cố và bắt đầu xử lý ngay. Cư dân có thể theo dõi quá trình xử lý trực tiếp trên app.",
    ticketTitle: "KTV đang tới căn hộ",
    ticketStatus: "Đang xử lý",
    ticketNote: "Đã nhận việc",
  },
] as const;

type FixitSlide = typeof fixitSlides[number];

type Phase = "idle" | "pre" | "run";
type SlideState = "idle" | "in-pre" | "in-run" | "out-pre" | "out-run" | "off";

/** Depth cut: the outgoing slide steps forward for a beat, then both planes travel in z. */
function slideState(index: number, active: number, previous: number | null, phase: Phase): SlideState {
  if (index === active) return previous !== null && phase === "pre" ? "in-pre" : phase === "run" ? "in-run" : "idle";
  if (index === previous) return phase === "pre" ? "out-pre" : "out-run";
  return "off";
}

export function CinematicLogin({
  email,
  password,
  error,
  busy,
  showPassword,
  onEmailChange,
  onPasswordChange,
  onPasswordVisibilityChange,
  onSubmit,
}: CinematicLoginProps) {
  const [activeSlide, setActiveSlide] = useState(0);
  const [previousSlide, setPreviousSlide] = useState<number | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [direction, setDirection] = useState(1);

  const stageRef = useRef<HTMLElement>(null);
  const trackRefs = useRef<(HTMLElement | null)[]>([]);
  const timers = useRef<number[]>([]);
  const overPromo = useRef(false);
  const overCard = useRef(false);
  const elapsed = useRef(0);
  const lastFrame = useRef(0);
  const activeRef = useRef(0);
  activeRef.current = activeSlide;

  const goTo = useCallback((next: number, nextDirection: number) => {
    const target = ((next % fixitSlides.length) + fixitSlides.length) % fixitSlides.length;
    if (target === activeRef.current) return;
    elapsed.current = 0;
    timers.current.forEach(window.clearTimeout);
    timers.current = [];
    setDirection(nextDirection);
    setPreviousSlide(activeRef.current);
    setActiveSlide(target);
    setPhase("pre");
    timers.current.push(window.setTimeout(() => setPhase("run"), PRE_PHASE));
    timers.current.push(window.setTimeout(() => {
      setPhase("idle");
      setPreviousSlide(null);
    }, RUN_PHASE));
  }, []);

  useEffect(() => () => timers.current.forEach(window.clearTimeout), []);

  // Autoplay and the progress bars share one rAF loop, so the fill lands exactly on the cut.
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      trackRefs.current.forEach((element, index) => {
        if (element) element.style.width = index === activeRef.current ? "100%" : "0%";
      });
      return;
    }
    let frame = 0;
    const tick = (now: number) => {
      const delta = lastFrame.current ? now - lastFrame.current : 0;
      lastFrame.current = now;
      if (!(overPromo.current && !overCard.current)) elapsed.current += delta;
      const duration = activeRef.current === fixitSlides.length - 1 ? FINAL_SLIDE_DURATION : SLIDE_DURATION;
      const progress = Math.min(1, elapsed.current / duration);
      trackRefs.current.forEach((element, index) => {
        if (element) element.style.width = `${(index === activeRef.current ? progress * 100 : 0).toFixed(1)}%`;
      });
      if (progress >= 1) goTo(activeRef.current + 1, 1);
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [goTo]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "ArrowLeft") goTo(activeRef.current - 1, -1);
      if (event.key === "ArrowRight") goTo(activeRef.current + 1, 1);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [goTo]);

  const resetParallax = useCallback(() => {
    stageRef.current?.style.setProperty("--cinematic-x", "0");
    stageRef.current?.style.setProperty("--cinematic-y", "0");
  }, []);

  const updateParallax = (event: ReactPointerEvent<HTMLElement>) => {
    if (overCard.current || window.innerWidth < 760) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const stage = stageRef.current;
    if (!stage) return;
    const rect = stage.getBoundingClientRect();
    stage.style.setProperty("--cinematic-x", (((event.clientX - rect.left) / rect.width) * 2 - 1).toFixed(3));
    stage.style.setProperty("--cinematic-y", (((event.clientY - rect.top) / rect.height) * 2 - 1).toFixed(3));
  };

  const activeFixitSlide = fixitSlides[activeSlide % fixitSlides.length];

  return (
    <main
      className={styles.fixitRoot}
      ref={stageRef}
      onPointerMove={updateParallax}
      onPointerLeave={() => { overPromo.current = false; resetParallax(); }}
    >
      <section
        className={styles.fixitPromo}
        aria-label="Giới thiệu FixIt"
        onPointerEnter={() => { overPromo.current = true; }}
      >
        <div className={styles.fixitLockup}>
          <span className={styles.fixitLogo}><Wrench size={18} /></span>
          <strong>FixIt</strong>
          <i />
          <small>One Access</small>
        </div>

        <div className={styles.fixitPromoBody}>
          <article className={styles.fixitCopy} key={activeFixitSlide.eyebrow}>
            <p>{activeFixitSlide.eyebrow}</p>
            <h1>{activeFixitSlide.title}</h1>
            <h2>{activeFixitSlide.body}</h2>
          </article>

          <div className={styles.fixitDeck} aria-hidden="true">
            <PhonePreview sceneIndex={activeSlide} />
            <div className={styles.fixitTilt}>
              <div className={styles.fixitPhone}>
                <div className={styles.fixitScreen}>
                  <span className={styles.fixitIsland} />
                  <header className={styles.fixitAppbar}>
                    <span><Wrench size={15} /></span>
                    <b>FixIt</b>
                    <em>Live</em>
                  </header>

                  <div className={styles.fixitPhoneBody}>
                    <div className={styles.fixitSearch}>
                      <Mail size={12} />
                      <span>Tìm theo mã hoặc nội dung phản ánh</span>
                    </div>
                    <div className={styles.fixitFilters}>
                      <b>Tất cả</b>
                      <span>Đang theo dõi</span>
                      <span>Đã kết thúc</span>
                    </div>
                    <div className={styles.fixitCountRow}>
                      <span>12 phản ánh</span>
                      <strong>Hôm nay</strong>
                    </div>

                    <article className={styles.fixitMiniTicket}>
                      <header>
                        <div>
                          <b>{activeFixitSlide.ticketTitle}</b>
                          <p>{activeFixitSlide.ticketNote}</p>
                        </div>
                        <span>{activeFixitSlide.ticketStatus}</span>
                      </header>
                      <footer>
                        <strong>11:24</strong>
                        <em>KTV dự kiến bắt đầu xử lý lúc 12:24 · 28 Th8</em>
                      </footer>
                    </article>

                    <article className={styles.fixitMiniTicket}>
                      <header>
                        <div>
                          <b>Chiếu sáng khu vực chung</b>
                          <p>Hành lang · Tầng 7</p>
                        </div>
                        <span>Đã duyệt</span>
                      </header>
                      <footer>
                        <strong>11:15</strong>
                        <em>Ban quản lý đang điều phối</em>
                      </footer>
                    </article>
                  </div>

                  <nav className={styles.fixitTabbar}>
                    <span><ShieldCheck size={15} />Trang chủ</span>
                    <span><BellRing size={15} />Thông báo</span>
                    <button type="button" tabIndex={-1}>+</button>
                    <span className={styles.fixitTabActive}><Check size={15} />Phản ánh</span>
                    <span><UserRound size={15} />Tôi</span>
                  </nav>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className={styles.fixitDots} aria-label="Chọn nội dung giới thiệu">
          {fixitSlides.map((item, index) => (
            <button
              key={item.eyebrow}
              type="button"
              className={index === activeSlide ? styles.fixitDotActive : ""}
              aria-label={`Nội dung ${index + 1}`}
              aria-current={index === activeSlide ? "true" : undefined}
              onClick={() => goTo(index, index > activeSlide ? 1 : -1)}
            >
              <i ref={(element) => { trackRefs.current[index] = element; }} />
            </button>
          ))}
        </div>
      </section>

      <section
        className={styles.fixitPanel}
        onPointerEnter={() => { overCard.current = true; resetParallax(); }}
        onPointerLeave={() => { overCard.current = false; }}
      >
        <form className={styles.fixitForm} onSubmit={onSubmit}>
          <header className={styles.fixitFormBrand}>
            <span><Wrench size={19} /></span>
            <div><strong>FixIt</strong><small>One Access</small></div>
          </header>

          <div className={styles.fixitIntro}>
            <h2>Đăng nhập</h2>
            <p>Nhập email và mật khẩu được Ban quản lý cấp.</p>
          </div>

          {error ? <div className={styles.fixitError} role="alert">{error}</div> : null}

          <label className={styles.fixitField} htmlFor="unified-email">
            <span>Email</span>
            <div>
              <input
                id="unified-email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => onEmailChange(event.target.value)}
                placeholder="ten@fixit.vn"
                required
              />
              <Mail size={17} aria-hidden="true" />
            </div>
          </label>

          <label className={styles.fixitField} htmlFor="unified-password">
            <span>Mật khẩu</span>
            <div>
              <input
                id="unified-password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                value={password}
                onChange={(event) => onPasswordChange(event.target.value)}
                placeholder="••••••••"
                required
              />
              <button
                type="button"
                aria-label={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"}
                onClick={onPasswordVisibilityChange}
              >
                {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
              </button>
            </div>
          </label>

          <button className={styles.fixitSubmit} type="submit" disabled={busy}>
            {busy ? "Đang xác thực..." : <>Đăng nhập <ArrowRight size={17} /></>}
          </button>

          <p className={styles.fixitFoot}>
            <ShieldCheck size={15} />
            Hệ thống tự động nhận diện vai trò sau khi đăng nhập.
          </p>
        </form>
      </section>
    </main>
  );

  return (
    <main
      className={styles.stage}
      ref={stageRef}
      onPointerMove={updateParallax}
      onPointerLeave={() => { overPromo.current = false; resetParallax(); }}
    >
      <div className={styles.ambient} aria-hidden="true" />
      <section
        className={styles.promo}
        aria-label="Giới thiệu FixIt"
        onPointerEnter={() => { overPromo.current = true; }}
      >
        {slides.map((item, index) => (
          <article
            key={item.kind}
            className={styles.slide}
            data-state={slideState(index, activeSlide, previousSlide, phase)}
            data-kind={item.kind}
            aria-hidden={index === activeSlide ? undefined : "true"}
            style={{ "--cinematic-dir": direction } as CSSProperties}
          >
            <div className={`${styles.layer} ${styles.far}`} aria-hidden="true">
              <i className={styles.halo} />
              <i className={styles.grid} />
              <i className={styles.orbit} />
              <i className={styles.orbitDashed} />
              <i className={styles.rule} />
              <i className={styles.ruleLow} />
            </div>
            <div className={`${styles.layer} ${styles.mid}`} aria-hidden="true" />
            <div className={`${styles.layer} ${styles.main}`}>
              <div className={styles.slideContent}>
                <p className={styles.eyebrow}>{item.eyebrow}</p>
                <h1 className={styles.title}>{item.title}</h1>
                <h2 className={styles.body}>{item.body}</h2>
              </div>
              <div className={styles.showcase}>
                {item.kind === "assignment" ? <AssignmentCard /> : item.kind === "routing" ? <RoutingCard /> : <ExpertCard />}
              </div>
            </div>
            <div className={`${styles.layer} ${styles.fore}`} aria-hidden="true">
              <i className={styles.speckDot} />
              <i className={styles.speckBar} />
              <i className={styles.speckOrb} />
            </div>
          </article>
        ))}

        <div className={styles.slideControls}>
          <div className={styles.tracks} aria-label="Chọn nội dung giới thiệu">
            {slides.map((item, index) => (
              <button
                key={item.kind}
                type="button"
                className={index === activeSlide ? styles.trackActive : ""}
                aria-label={`Nội dung ${index + 1}`}
                aria-current={index === activeSlide ? "true" : undefined}
                onClick={() => goTo(index, index > activeSlide ? 1 : -1)}
              >
                <i ref={(element) => { trackRefs.current[index] = element; }} />
              </button>
            ))}
          </div>
          <span>{String((activeSlide % slides.length) + 1).padStart(2, "0")} / 03</span>
          <div className={styles.arrows}>
            <button type="button" aria-label="Nội dung trước" onClick={() => goTo(activeSlide - 1, -1)}><ArrowLeft size={17} /></button>
            <button type="button" aria-label="Nội dung tiếp theo" onClick={() => goTo(activeSlide + 1, 1)}><ArrowRight size={17} /></button>
          </div>
        </div>
      </section>

      <div className={styles.vignette} aria-hidden="true" />

      <section
        className={styles.loginArea}
        onPointerEnter={() => { overCard.current = true; resetParallax(); }}
        onPointerLeave={() => { overCard.current = false; }}
      >
        <form className={styles.loginCard} onSubmit={onSubmit}>
          <header>
            <span className={styles.loginMark}><Wrench size={20} /></span>
            <div><strong>FixIt</strong><small>ONE ACCESS</small></div>
          </header>
          <div className={styles.loginIntro}><h2>Đăng nhập</h2><p>Nhập email và mật khẩu được Ban quản lý cấp.</p></div>
          <div className={styles.formError} data-open={error ? "true" : "false"} role="alert">{error}</div>
          <label className={styles.field} htmlFor="unified-email"><span>Email</span><div><input id="unified-email" type="email" autoComplete="email" value={email} onChange={(event) => onEmailChange(event.target.value)} placeholder="ten@fixit.vn" required /><Mail size={17} aria-hidden="true" /></div></label>
          <label className={styles.field} htmlFor="unified-password"><span>Mật khẩu</span><div><input id="unified-password" type={showPassword ? "text" : "password"} autoComplete="current-password" value={password} onChange={(event) => onPasswordChange(event.target.value)} placeholder="••••••••" required /><button type="button" aria-label={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"} onClick={onPasswordVisibilityChange}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></label>
          <button className={styles.submit} type="submit" disabled={busy}>{busy ? "Đang xác thực…" : <>Đăng nhập <ArrowRight size={17} /></>}</button>
          <p className={styles.autoRole}><ShieldCheck size={15} />Hệ thống sẽ tự động nhận diện vai trò của bạn.</p>
        </form>
      </section>
    </main>
  );
}

function PhonePreview({ sceneIndex }: { sceneIndex: number }) {
  if (sceneIndex === 3) return <TechnicianPreview />;
  if (sceneIndex === 1) return <AgentScene />;
  if (sceneIndex === 2) return <TicketListScene />;
  return <HomeScene />;
}

function PhoneShell({
  title,
  activeTab,
  children,
  pulseFab = false,
}: {
  title: string;
  activeTab: "home" | "reports";
  children: ReactNode;
  pulseFab?: boolean;
}) {
  return (
    <div className={styles.fixitPhoneReplica}>
      <div className={styles.fixitPhoneFrame}>
        <div className={styles.fixitPhoneGlass}>
          <span className={styles.fixitPhoneIsland} />

          <header className={styles.fixitPhoneHeader}>
            <span className={styles.fixitPhoneMark}><Wrench size={17} /></span>
            <b>{title}</b>
            {title === "Trang chủ" ? <em>Live</em> : null}
          </header>

          {children}

          <nav className={styles.fixitPhoneTabs}>
            <span className={activeTab === "home" ? styles.fixitPhoneTabActive : ""}><Home size={16} />Trang chủ</span>
            <span><span className={styles.fixitPhoneBell}><BellRing size={16} /><i>2</i></span>Thông báo</span>
            <span />
            <span className={activeTab === "reports" ? styles.fixitPhoneTabActive : ""}><ClipboardList size={16} />Phản ánh</span>
            <span><UserRound size={16} />Tài khoản</span>
            <button className={styles.fixitPhoneFab} type="button" tabIndex={-1}>
              <Plus size={21} />
            </button>
          </nav>
          {pulseFab ? (
            <>
              <span className={styles.fixitPhoneRipple} />
              <span className={styles.fixitPhoneFabRing} />
              <span className={styles.fixitFinger}>
                <svg width="46" height="60" viewBox="0 0 46 60" fill="none" aria-hidden="true">
                  <path
                    d="M14 30V9a5 5 0 0 1 10 0v16M24 25v-4a4.5 4.5 0 0 1 9 0v12M14 30c-4-3-9-2-9 3 0 8 4 16 9 20 3 2.6 6 3 10 3h5a10 10 0 0 0 10-10V27a4.5 4.5 0 0 0-9 0"
                    fill="#f6c9a6"
                    stroke="#d9a87e"
                    strokeWidth="1.6"
                    strokeLinejoin="round"
                  />
                </svg>
              </span>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function HomeScene() {
  return (
    <PhoneShell title="Trang chủ" activeTab="home" pulseFab>
      <main className={`${styles.fixitPhoneContent} ${styles.fixitPhoneHomeContent}`}>
        <section className={styles.fixitPhoneGreeting}>
          <h3>Xin chào, Cư dân A-1203</h3>
          <p>Căn hộ 1203</p>
        </section>

        <article className={styles.fixitEmergencyCard}>
          <span className={styles.fixitEmergencyIcon}><PhoneCall size={16} /></span>
          <div>
            <header>
              <b>Sự cố nguy hiểm</b>
              <em>Khẩn cấp</em>
            </header>
            <p>Cháy, khói, rò điện hoặc rò gas: gọi Ban quản lý ngay.</p>
            <button type="button" tabIndex={-1}><PhoneCall size={13} />Gọi Ban quản lý</button>
          </div>
        </article>

        <article className={styles.fixitStepsCard}>
          <b>Gửi phản ánh trong vài bước</b>
          <div>
            <span>1</span>
            <MapPin size={15} />
            <p><strong>Chọn vị trí</strong><small>Cho biết nơi xảy ra sự cố.</small></p>
          </div>
          <div>
            <span>2</span>
            <MessageSquare size={15} />
            <p><strong>Mô tả ngắn gọn</strong><small>Chia sẻ điều bạn cần hỗ trợ.</small></p>
          </div>
          <div>
            <span>3</span>
            <Camera size={15} />
            <p><strong>Thêm ảnh nếu có</strong><small>Giúp Ban quản lý xử lý nhanh hơn.</small></p>
          </div>
        </article>

        <article className={styles.fixitPrivacyCard}>
          <ShieldCheck size={17} />
          <p><b>Thông tin của bạn được giữ riêng.</b> Chỉ người xử lý được xem phản ánh của căn hộ bạn.</p>
        </article>
      </main>
    </PhoneShell>
  );
}

function AgentScene() {
  return (
    <PhoneShell title="Phản ánh mới" activeTab="reports">
      <main className={`${styles.fixitPhoneContent} ${styles.fixitPhoneAgentContent}`}>
        <article className={styles.fixitAgentCard}>
          <span className={styles.fixitAgentBadge}><Sparkles size={13} />FixIt Agent</span>
          <div className={styles.fixitAgentChips}>
            <span>Vị trí</span>
            <span>Mô tả</span>
            <span>Ảnh</span>
          </div>
          <div className={styles.fixitAgentTrack}>
            {["Đang đọc", "Đã phân loại", "Đã gửi Ban quản lý"].map((label) => (
              <div className={styles.fixitAgentRow} key={label}>
                <span><Check size={12} /></span>
                <b>{label}</b>
              </div>
            ))}
          </div>
        </article>
      </main>
    </PhoneShell>
  );
}

function TicketListScene() {
  return (
    <PhoneShell title="Phản ánh" activeTab="reports">
      <main className={`${styles.fixitPhoneContent} ${styles.fixitPhoneListContent}`}>
        <div className={styles.fixitPhoneSearch}>
          <Search size={15} />
          <span>Tìm mã hoặc nội dung phản ánh</span>
        </div>

        <div className={styles.fixitPhoneFilters}>
          <strong>Tất cả</strong>
          <span>Đang theo dõi</span>
          <span>Đã kết thúc</span>
        </div>

        <div className={styles.fixitPhoneMeta}>
          <span>12 phản ánh</span>
          <button type="button" tabIndex={-1}>Bộ lọc</button>
        </div>

        <p className={styles.fixitPhoneToday}>Hôm nay</p>

        <div className={styles.fixitTicketViewport}>
          <div className={styles.fixitTicketStack}>
            <article className={styles.fixitPhoneTicket}>
              <header>
                <div>
                  <b>Ồn ào</b>
                  <p>Hành lang người ta tổ chức ăn uống</p>
                </div>
                <span className={`${styles.fixitPhoneStatusOk} ${styles.fixitPhoneStatusPulse}`}>Đã có KTV</span>
              </header>
              <footer>
                <strong>11:24</strong>
                <em>Dự kiến: 12h24</em>
              </footer>
            </article>

            <article className={styles.fixitPhoneTicket}>
              <header>
                <div>
                  <b>An ninh / An toàn</b>
                  <p>Cửa thoát hiểm tầng 7 bị chèn kê</p>
                </div>
                <span>BQL xem xét</span>
              </header>
              <footer>
                <strong className={styles.fixitPhoneTimeBlue}>11:15</strong>
                <em>Chờ BQL</em>
              </footer>
            </article>

            <article className={styles.fixitPhoneTicket}>
              <header>
                <div>
                  <b>Nước</b>
                  <p>Vòi chữa cháy bị thoát nước ra ngoài</p>
                </div>
                <span className={styles.fixitPhoneStatusOk}>Đã có KTV</span>
              </header>
              <footer>
                <strong>11:14</strong>
                <em>Dự kiến: 11h14</em>
              </footer>
            </article>
          </div>
        </div>
      </main>
    </PhoneShell>
  );
}

function TechnicianPreview() {
  return (
    <div className={styles.fixitPhoneReplica}>
      <div className={styles.fixitTechnicianScene}>
        <svg viewBox="0 0 300 618" width="300" height="618" aria-hidden="true">
          <rect x="118" y="150" width="182" height="352" rx="10" fill="#e7edf6" />
          <rect x="0" y="502" width="300" height="116" rx="10" fill="#dce4f0" />
          <path d="M0 502h300" stroke="#cbd6e6" strokeWidth="3" />
          <rect x="160" y="196" width="126" height="306" rx="6" fill="#c9d6e8" />
          <rect x="169" y="205" width="108" height="288" rx="4" fill="#b4c6de" />
          <rect x="194" y="226" width="58" height="18" rx="4" fill="#e7edf6" />
          <circle cx="180" cy="356" r="6" fill="#8fa6c4" />
          <g className={styles.fixitWalk}>
            <g className={styles.fixitLegA}>
              <rect x="120" y="442" width="12" height="60" rx="6" fill="#33465f" />
            </g>
            <g className={styles.fixitLegB}>
              <rect x="135" y="442" width="12" height="60" rx="6" fill="#27384d" />
            </g>
            <rect className={styles.fixitToolbox} x="87" y="414" width="42" height="32" rx="5" fill="#e4a93c" />
            <rect className={styles.fixitToolbox} x="87" y="414" width="42" height="9" rx="4" fill="#c98f26" />
            <rect className={styles.fixitArmBack} x="111" y="364" width="12" height="56" rx="6" fill="#1d4ed8" />
            <rect x="113" y="344" width="41" height="106" rx="14" fill="#2c6bee" />
            <rect x="113" y="344" width="41" height="20" rx="10" fill="#1d4ed8" />
            <circle cx="133" cy="320" r="20" fill="#f6c9a6" />
            <path d="M113 317a20 20 0 0 1 40 0z" fill="#2e3d52" />
            <g className={styles.fixitArmKnock}>
              <rect x="149" y="363" width="12" height="56" rx="6" fill="#3f7bf5" />
              <circle cx="155" cy="422" r="8" fill="#f6c9a6" />
            </g>
          </g>
        </svg>
      </div>
    </div>
  );
}

function AssignmentCard() {
  return <article className={styles.assignmentCard}>
    <header><span><BellRing size={18} /></span><div><b>Phản ánh mới</b><small>Tòa A · Căn 12.04</small></div><em>ĐÃ PHÂN</em></header>
    <section><div><span><UserRound size={15} /></span><p><b>Nhóm Kỹ thuật</b><small>Phân việc tự động</small></p></div><footer><span><Clock3 size={14} />Cam kết hoàn thành</span><b>trong 4 giờ</b></footer></section>
    <p className={styles.cardLocation}><MapPin size={14} />Hầm B1 · Khu vực thang máy</p>
    <div className={styles.cardLines}><i /><i /></div>
    <footer className={styles.cardFooter}><span><i />Cư dân gửi</span><b>Đã tiếp nhận</b></footer>
  </article>;
}

function RoutingCard() {
  return <article className={styles.routingCard}>
    <i className={styles.routingOrbit} /><i className={styles.routingOrbitInner} />
    <div className={styles.routeTicket}><em>#1042</em><b>Rò rỉ nước tầng hầm</b><i /></div>
    <svg className={styles.routePaths} viewBox="0 0 505 300" fill="none" aria-hidden="true"><path d="M145 150C248 150 258 52 360 52" /><path d="M145 150H360" className={styles.routeActive} /><path d="M145 150C248 150 258 248 360 248" /></svg>
    <div className={styles.routeNode}><span><Check size={17} /></span><b>Kỹ thuật</b><small>Nhóm phụ trách</small></div>
    <div className={`${styles.routePill} ${styles.routePillTop}`}>Vệ sinh</div><div className={`${styles.routePill} ${styles.routePillBottom}`}>An ninh</div>
  </article>;
}

function ExpertCard() {
  return <article className={styles.progressCard}>
    <header><div><em>PHIẾU #1042</em><b>Rò rỉ nước tầng hầm</b></div><span>HOÀN TẤT</span></header>
    <ol>
      <li><i><Check size={13} /></i><p><b>Tiếp nhận</b><small>08:12 · Cư dân gửi</small></p></li>
      <li><i><Check size={13} /></i><p><b>Phân loại</b><small>08:15 · Nhóm Kỹ thuật</small></p></li>
      <li><i><Check size={13} /></i><p><b>Đang xử lý</b><small>09:40 · Kỹ thuật viên</small></p></li>
      <li className={styles.progressCurrent}><i><Check size={13} /></i><p><b>Hoàn tất</b><small>11:05 · Đã xác nhận</small></p></li>
    </ol>
  </article>;
}
