type IconName =
  | "search"
  | "upload"
  | "people"
  | "alert"
  | "check"
  | "file"
  | "arrow"
  | "stop";
const paths: Record<IconName, React.ReactNode> = {
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="7.5" />
      <path d="m16 16 5 5" />
    </>
  ),
  upload: (
    <>
      <path d="M12 16V3m-5 5 5-5 5 5M3 14v7h18v-7" />
    </>
  ),
  people: (
    <>
      <circle cx="9" cy="7" r="3" />
      <path d="M3 21v-4a6 6 0 0 1 12 0v4M16 4a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 4v3" />
    </>
  ),
  alert: (
    <>
      <path d="m12 3 10 18H2L12 3Z" fill="currentColor" />
      <path d="M12 9v5m0 3h.01" stroke="white" />
    </>
  ),
  check: (
    <>
      <circle cx="12" cy="12" r="10" fill="currentColor" />
      <path d="m7 12 3 3 7-7" stroke="white" />
    </>
  ),
  file: (
    <>
      <path d="M14 2H5v20h14V7l-5-5Z" />
      <path d="M14 2v5h5" />
    </>
  ),
  arrow: <path d="m8 4 8 8-8 8" />,
  stop: <rect x="5" y="5" width="14" height="14" rx="1" />,
};
export default function Icon({
  name,
  size = 22,
}: {
  name: IconName;
  size?: number;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}
