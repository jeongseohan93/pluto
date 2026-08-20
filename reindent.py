import io

p = "desktop/src/domains/graph-view/ui/FunctionGraphSurface.tsx"
lines = io.open(p, encoding="utf-8").read().split("\n")
for i in range(1089, 1326):
    if lines[i].strip():
        lines[i] = "  " + lines[i]
io.open(p, "w", encoding="utf-8", newline="").write("\n".join(lines))
print(repr(lines[1089][:60]))
print(repr(lines[1325][:60]))
