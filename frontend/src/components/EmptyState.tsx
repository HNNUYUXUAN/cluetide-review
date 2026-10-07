import Icon from "./Icon";
export default function EmptyState({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="empty-state">
      <Icon name="search" size={32} />
      <h2>{title}</h2>
      <p>{children}</p>
    </div>
  );
}
