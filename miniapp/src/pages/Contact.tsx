// src/pages/Contact.tsx
import BackBar from "../components/BackBar";

export default function Contact() {
  return (
    <div className="min-h-dvh flex flex-col">
      <BackBar title="Связаться" to="/" />
      <div className="px-5 pb-24 pt-4 max-w-[720px] mx-auto w-full">
        <article className="rounded-3xl bg-white/90 p-5 leading-relaxed text-[15px] md:text-base">
          <p className="mb-3">
            Если что-то не работает, есть идея или хочется дать обратную связь —
            напиши нам, мы отвечаем.
          </p>

          <ul className="space-y-2">
            <li>
              ✉️ Почта:{" "}
              <a href="mailto:selflect@proton.me" className="underline">
                selflect@proton.me
              </a>
            </li>
            <li>
              💬 Telegram:{" "}
              <a
                href="https://t.me/pomniai?direct"
                target="_blank"
                rel="noreferrer"
                className="underline"
              >
                t.me/pomniai?direct
              </a>
            </li>
          </ul>

          <p className="mt-4 text-black/60 text-sm">
            Спасибо, что помогаешь нам становиться лучше ✨
          </p>
        </article>
      </div>
    </div>
  );
}
