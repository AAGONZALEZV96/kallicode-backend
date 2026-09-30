import React from "react";

interface WidgetProps {
  title: string;
  onAction: (id: string) => void;
  items: { id: string; label: string }[];
}

export const Widget: React.FC<WidgetProps> = ({ title, onAction, items }) => {
  const handleClick = (id: string) => {
    onAction(id);
  };

  return (
    <div className="widget">
      <h2>{title}</h2>
      <ul>
        {items.map((item) => (
          <li key={item.id} onClick={() => handleClick(item.id)}>
            {item.label}
          </li>
        ))}
      </ul>
    </div>
  );
};