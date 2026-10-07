import type { ComponentProps } from "react";
import { appRouter } from "./browser-router";
import { routeHref } from "./routes";
import type { NavigableRoute } from "./routes";

type Props = Omit<ComponentProps<"a">, "href"> & {
  route: NavigableRoute;
  replace?: boolean;
};

export default function RouteLink({ route, replace, onClick, ...props }: Props) {
  return <a {...props} href={`#${routeHref(route)}`} onClick={(event) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey ||
        event.shiftKey || event.altKey || (props.target && props.target !== "_self") ||
        props.download !== undefined) return;
    event.preventDefault();
    appRouter().navigate(route, { replace });
  }} />;
}
