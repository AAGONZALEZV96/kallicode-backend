import React, { Component, useState } from "react";

type ListProps<T> = {
  items: T[];
  render: (item: T) => React.ReactNode;
};

export const GenericList = <T,>({ items, render }: ListProps<T>) => {
  const [selected, setSelected] = useState<number>(-1);
  return (
    <ul>
      {items.map((item, i) => (
        <li key={i} onClick={() => setSelected(i)} className={i === selected ? "on" : ""}>
          {render(item)}
        </li>
      ))}
    </ul>
  );
};

export class Counter extends Component<{ start: number }, { value: number }> {
  state = { value: this.props.start };

  increment = () => this.setState({ value: this.state.value + 1 });

  render() {
    return <button onClick={this.increment}>{this.state.value}</button>;
  }
}

export function Widget({ title }: { title: string }) {
  return (
    <section>
      <h2>{title}</h2>
      <Counter start={0} />
      <GenericList items={["a", "b"]} render={(s) => <span>{s}</span>} />
    </section>
  );
}
