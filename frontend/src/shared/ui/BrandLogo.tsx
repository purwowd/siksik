type Size = "sm" | "lg";

type Props = {
  size?: Size;
};

/** Lockup SATRIA — wordmark tegas + accent bar (tanpa ikon/gradient clip). */
export function BrandLogo({ size = "sm" }: Props) {
  const NameTag = size === "lg" ? "h1" : "strong";

  return (
    <div className={`brand-logo brand-logo--${size}`}>
      <span className="brand-logo-accent" aria-hidden />
      <NameTag className="brand-logo-name">SATRIA</NameTag>
    </div>
  );
}
