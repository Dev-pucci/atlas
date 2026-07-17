import { login } from "./actions";

export default function LoginPage({
  searchParams,
}: {
  searchParams: { next?: string; error?: string };
}) {
  const next = searchParams.next ?? "/";
  const failed = searchParams.error === "1";

  return (
    <main style={styles.wrap}>
      <form action={login} style={styles.card}>
        <h1 style={styles.title}>Atlas Annotator</h1>
        <input type="hidden" name="next" value={next} />
        <label style={styles.label} htmlFor="password">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoFocus
          required
          style={styles.input}
        />
        {failed && <p style={styles.error}>Wrong password.</p>}
        <button type="submit" style={styles.button}>
          Sign in
        </button>
      </form>
    </main>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: {
    minHeight: "100vh",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "#f4f5f7",
    fontFamily: "Segoe UI, Arial, sans-serif",
  },
  card: {
    background: "#fff",
    padding: 32,
    borderRadius: 10,
    boxShadow: "0 2px 12px rgba(0,0,0,.12)",
    width: 320,
  },
  title: { margin: "0 0 20px", fontSize: 20, color: "#1a1a2e" },
  label: { display: "block", fontSize: 13, color: "#555", marginBottom: 6 },
  input: {
    width: "100%",
    padding: "10px 12px",
    fontSize: 15,
    border: "1px solid #ccc",
    borderRadius: 6,
    boxSizing: "border-box",
    marginBottom: 14,
  },
  button: {
    width: "100%",
    padding: "10px 14px",
    fontSize: 15,
    border: 0,
    borderRadius: 6,
    background: "#2b5fd9",
    color: "#fff",
    cursor: "pointer",
  },
  error: { color: "#c0392b", fontSize: 13, margin: "-6px 0 12px" },
};
