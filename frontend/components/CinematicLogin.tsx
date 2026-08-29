"use client";

import {
  ArrowLeft,
  ArrowRight,
  BellRing,
  Check,
  Clock3,
  Eye,
  EyeOff,
  Mail,
  MapPin,
  ShieldCheck,
  UserRound,
  Wrench,
} from "lucide-react";
import { FormEvent, PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from "react";
import type { BackendRole } from "@/config/api";
import styles from "./CinematicLogin.module.css";

type DemoLoginAccount = {
  role: BackendRole;
  label: string;
  email: string;
  password: string;
};

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
  demoAccounts?: DemoLoginAccount[];
  onDemoLogin?: (account: DemoLoginAccount) => void;
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

const demoRoleColumns: Array<{ role: BackendRole; title: string }> = [
  { role: "resident", title: "Cư dân" },
  { role: "manager", title: "BQL" },
  { role: "technician", title: "KTV" },
];

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
  demoAccounts = [],
  onDemoLogin,
}: CinematicLoginProps) {
  const [activeSlide, setActiveSlide] = useState(0);
  const [paused, setPaused] = useState(false);
  const stageRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (paused || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const timer = window.setInterval(() => setActiveSlide((current) => (current + 1) % slides.length), 6000);
    return () => window.clearInterval(timer);
  }, [paused]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "ArrowLeft") setActiveSlide((current) => (current + slides.length - 1) % slides.length);
      if (event.key === "ArrowRight") setActiveSlide((current) => (current + 1) % slides.length);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const updateParallax = (event: ReactPointerEvent<HTMLElement>) => {
    if (window.innerWidth < 760) return;
    const stage = stageRef.current;
    if (!stage) return;
    const rect = stage.getBoundingClientRect();
    stage.style.setProperty("--cinematic-x", String(((event.clientX - rect.left) / rect.width) * 2 - 1));
    stage.style.setProperty("--cinematic-y", String(((event.clientY - rect.top) / rect.height) * 2 - 1));
  };

  const resetParallax = () => {
    stageRef.current?.style.setProperty("--cinematic-x", "0");
    stageRef.current?.style.setProperty("--cinematic-y", "0");
  };

  const slide = slides[activeSlide];
  const demoColumns = demoRoleColumns
    .map((column) => ({ ...column, accounts: demoAccounts.filter((account) => account.role === column.role) }))
    .filter((column) => column.accounts.length > 0);
  return (
    <main
      className={styles.stage}
      ref={stageRef}
      onPointerMove={updateParallax}
      onPointerLeave={resetParallax}
    >
      <div className={styles.ambient} aria-hidden="true" />
      <section className={styles.promo} aria-label="Giới thiệu FixIt" onPointerEnter={() => setPaused(true)} onPointerLeave={() => setPaused(false)}>
        <div className={styles.slideContent} key={slide.kind}>
          <p>{slide.eyebrow}</p>
          <h1>{slide.title}</h1>
          <div className={styles.showcase}>{slide.kind === "assignment" ? <AssignmentCard /> : slide.kind === "routing" ? <RoutingCard /> : <ExpertCard />}</div>
          <h2>{slide.body}</h2>
        </div>
        <div className={styles.slideControls}>
          <div className={styles.tracks} aria-label="Chọn nội dung giới thiệu">
            {slides.map((item, index) => <button key={item.kind} type="button" className={index === activeSlide ? styles.trackActive : ""} aria-label={`Nội dung ${index + 1}`} aria-current={index === activeSlide ? "true" : undefined} onClick={() => setActiveSlide(index)}><i /></button>)}
          </div>
          <span>{String(activeSlide + 1).padStart(2, "0")} / 03</span>
          <div className={styles.arrows}>
            <button type="button" aria-label="Nội dung trước" onClick={() => setActiveSlide((current) => (current + slides.length - 1) % slides.length)}><ArrowLeft size={17} /></button>
            <button type="button" aria-label="Nội dung tiếp theo" onClick={() => setActiveSlide((current) => (current + 1) % slides.length)}><ArrowRight size={17} /></button>
          </div>
        </div>
      </section>

      <section className={styles.loginArea} onPointerEnter={() => setPaused(true)} onPointerLeave={() => setPaused(false)}>
        <form className={styles.loginCard} onSubmit={onSubmit}>
          <header>
            <span className={styles.loginMark}><Wrench size={20} /></span>
            <div><strong>FixIt</strong><small>ONE ACCESS</small></div>
          </header>
          <div className={styles.loginIntro}><h2>Đăng nhập</h2><p>Nhập email và mật khẩu được Ban quản lý cấp.</p></div>
          {error && <div className={styles.formError} role="alert">{error}</div>}
          {demoAccounts.length > 0 && onDemoLogin ? (
            <div className={styles.demoAccounts} aria-label="Tài khoản mẫu">
              <span>Tài khoản mẫu</span>
              <div className={styles.demoColumns}>
                {demoColumns.map((column) => (
                  <section key={column.role}>
                    <h3>{column.title}</h3>
                    {column.accounts.map((account) => (
                      <button key={`${account.label}-${account.email}`} type="button" disabled={busy} onClick={() => onDemoLogin(account)}>
                        <strong>{account.label}</strong>
                        <small>{account.email}</small>
                      </button>
                    ))}
                  </section>
                ))}
              </div>
            </div>
          ) : null}
          <label className={styles.field} htmlFor="unified-email"><span>Email</span><div><input id="unified-email" type="email" autoComplete="email" value={email} onChange={(event) => onEmailChange(event.target.value)} placeholder="ten@fixit.vn" required /><Mail size={17} aria-hidden="true" /></div></label>
          <label className={styles.field} htmlFor="unified-password"><span>Mật khẩu</span><div><input id="unified-password" type={showPassword ? "text" : "password"} autoComplete="current-password" value={password} onChange={(event) => onPasswordChange(event.target.value)} placeholder="••••••••" required /><button type="button" aria-label={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"} onClick={onPasswordVisibilityChange}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></label>
          <button className={styles.submit} type="submit" disabled={busy}>{busy ? "Đang xác thực…" : <>Đăng nhập <ArrowRight size={17} /></>}</button>
          <p className={styles.autoRole}><ShieldCheck size={15} />Hệ thống sẽ tự động nhận diện vai trò của bạn.</p>
        </form>
      </section>
    </main>
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
