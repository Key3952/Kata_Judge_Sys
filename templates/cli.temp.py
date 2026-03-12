def open_judge_window(
    parent: tk.Tk,
    judge_id: int,
    judge_name: str,
    judge_disc: str,
    pair_id: int,
    pair_label: str,
    row_names,
    on_finished=None,
):
    top = tk.Toplevel(parent)
    top.title(f"Судья #{judge_id} — {judge_name} — пара #{pair_id}: {pair_label}")
    top.configure(bg=BG_MAIN)

    status_var = tk.StringVar(
        value=f"Судья #{judge_id} ({judge_name}), пара #{pair_id}: {pair_label}"
    )
    total_var = tk.StringVar(value="0")

    n_rows = len(row_names)

    minor1_vars = []
    minor2_vars = []
    medium_vars = []
    major_vars = []
    fail_vars = []
    corrections = [0.0] * n_rows
    row_scores = [float(MAX_POINTS)] * n_rows
    score_labels = []

    header_font = ("Segoe UI", 10, "bold")
    normal_font = ("Segoe UI", 10)

    def recalc_total():
        base_total = sum(row_scores)
        has_fail = any(v.get() == 1 for v in fail_vars)
        total = base_total / 2.0 if has_fail else base_total
        total_var.set(format_score(total))

    def recalc_row(row_index: int):
        minor_penalty = minor1_vars[row_index].get() * 1 + minor2_vars[row_index].get() * 1
        medium_penalty = medium_vars[row_index].get() * 3
        major_penalty = major_vars[row_index].get() * 5
        fail_penalty = fail_vars[row_index].get() * 10

        penalties = minor_penalty + medium_penalty + major_penalty + fail_penalty
        correction = corrections[row_index]

        score = MAX_POINTS - penalties + correction
        if score < 0:
            score = 0.0

        row_scores[row_index] = score
        score_labels[row_index].config(text=format_score(score))
        recalc_total()

    def make_penalty_button(parent_widget, text: str, var: tk.IntVar, row_idx: int) -> tk.Button:
        btn = tk.Button(
            parent_widget,
            text=text,
            width=4,
            height=1,
            font=("Segoe UI", 11, "bold"),
            bg=BTN_OFF_BG,
            fg=BTN_OFF_FG,
            relief="raised",
            borderwidth=1,
        )

        def cmd(v=var, b=btn, idx=row_idx):
            v.set(0 if v.get() else 1)
            style_toggle(b, bool(v.get()), negative=True)
            recalc_row(idx)

        btn.config(command=cmd)
        style_toggle(btn, False, negative=True)
        return btn

    container = tk.Frame(top, bg=BG_MAIN)
    container.pack(fill="both", expand=True)

    canvas = tk.Canvas(container, bg=BG_MAIN, highlightthickness=0)
    vscroll = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
    vscroll.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    canvas.configure(yscrollcommand=vscroll.set)

    scrollable_frame = tk.Frame(canvas, bg=TABLE_BG)

    def on_frame_configure(event):
        canvas.configure(scrollregion=canvas.bbox("all"))

    scrollable_frame.bind("<Configure>", on_frame_configure)
    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind_all("<MouseWheel>", _on_mousewheel)

    def header(text, row, col):
        tk.Label(
            scrollable_frame,
            text=text,
            font=header_font,
            bg=HEADER_BG,
            bd=1,
            relief="solid",
            padx=4,
            pady=2,
        ).grid(row=row, column=col, sticky="nsew")

    header("№", 0, 0)
    header("TECHNIQUES", 0, 1)
    header("Малая ошибка (2)", 0, 2)
    header("Средняя ошибка", 0, 3)
    header("Большая ошибка", 0, 4)
    header("Забытая техника", 0, 5)
    header("Поправка ±0.5", 0, 6)
    header("SCORE", 0, 7)

    header("", 1, 0)
    header("", 1, 1)
    header("1", 1, 2)
    header("3", 1, 3)
    header("5", 1, 4)
    header("10", 1, 5)
    header("±0.5", 1, 6)
    header("ИТОГ", 1, 7)

    for i, name in enumerate(row_names):
        row = i + 2

        tk.Label(
            scrollable_frame,
            text=str(i + 1),
            font=normal_font,
            bg=TABLE_BG,
            bd=1,
            relief="solid",
            padx=4,
            pady=2,
        ).grid(row=row, column=0, sticky="nsew")

        tk.Label(
            scrollable_frame,
            text=name,
            font=normal_font,
            bg=TABLE_BG,
            bd=1,
            relief="solid",
            padx=4,
            pady=2,
            anchor="w",
        ).grid(row=row, column=1, sticky="nsew")

        frame_minor = tk.Frame(scrollable_frame, bg=TABLE_BG)
        frame_minor.grid(row=row, column=2, sticky="nsew")

        m1_var = tk.IntVar(value=0)
        m2_var = tk.IntVar(value=0)

        btn_m1 = make_penalty_button(frame_minor, "-1", m1_var, i)
        btn_m1.pack(side=tk.LEFT, padx=2, pady=2)

        btn_m2 = make_penalty_button(frame_minor, "-1", m2_var, i)
        btn_m2.pack(side=tk.LEFT, padx=2, pady=2)

        minor1_vars.append(m1_var)
        minor2_vars.append(m2_var)

        med_var = tk.IntVar(value=0)
        maj_var = tk.IntVar(value=0)
        fail_var = tk.IntVar(value=0)

        med_btn = make_penalty_button(scrollable_frame, "-3", med_var, i)
        med_btn.grid(row=row, column=3, sticky="nsew", padx=1, pady=2)

        maj_btn = make_penalty_button(scrollable_frame, "-5", maj_var, i)
        maj_btn.grid(row=row, column=4, sticky="nsew", padx=1, pady=2)

        fail_btn = make_penalty_button(scrollable_frame, "-10", fail_var, i)
        fail_btn.grid(row=row, column=5, sticky="nsew", padx=1, pady=2)

        medium_vars.append(med_var)
        major_vars.append(maj_var)
        fail_vars.append(fail_var)

        frame_half = tk.Frame(scrollable_frame, bg=TABLE_BG)
        frame_half.grid(row=row, column=6, sticky="nsew")

        plus_btn = tk.Button(
            frame_half,
            text="+0.5",
            width=5,
            height=1,
            font=("Segoe UI", 9),
            borderwidth=1,
        )
        minus_btn = tk.Button(
            frame_half,
            text="-0.5",
            width=5,
            height=1,
            font=("Segoe UI", 9),
            borderwidth=1,
        )

        def update_corr_buttons(idx=i, btn_p=plus_btn, btn_m=minus_btn):
            corr = corrections[idx]
            style_toggle(btn_p, corr > 0, negative=False)
            style_toggle(btn_m, corr < 0, negative=True)

        def on_plus(idx=i, updater=update_corr_buttons):
            if corrections[idx] == 0.5:
                corrections[idx] = 0.0
            else:
                corrections[idx] = 0.5
            updater()
            recalc_row(idx)

        def on_minus(idx=i, updater=update_corr_buttons):
            if corrections[idx] == -0.5:
                corrections[idx] = 0.0
            else:
                corrections[idx] = -0.5
            updater()
            recalc_row(idx)

        plus_btn.config(command=on_plus)
        minus_btn.config(command=on_minus)
        update_corr_buttons()

        plus_btn.pack(side=tk.LEFT, padx=2, pady=2)
        minus_btn.pack(side=tk.LEFT, padx=2, pady=2)

        lbl_score = tk.Label(
            scrollable_frame,
            text=format_score(float(MAX_POINTS)),
            font=("Segoe UI", 11, "bold"),
            bg=TABLE_BG,
            bd=1,
            relief="solid",
            padx=4,
            pady=2,
        )
        lbl_score.grid(row=row, column=7, sticky="nsew")
        score_labels.append(lbl_score)

    for col in range(8):
        scrollable_frame.grid_columnconfigure(col, weight=1)

    bottom_frame = tk.Frame(top, bg=BG_MAIN)
    bottom_frame.pack(fill="x", padx=8, pady=(4, 4))

    total_frame = tk.Frame(bottom_frame, bg=BG_MAIN)
    total_frame.pack(side=tk.LEFT, anchor="w")

    tk.Label(
        total_frame,
        text="Общая сумма:",
        font=("Segoe UI", 11, "bold"),
        bg=BG_MAIN,
    ).pack(side=tk.LEFT)

    tk.Label(
        total_frame,
        textvariable=total_var,
        font=("Segoe UI", 11, "bold"),
        bg=BG_MAIN,
    ).pack(side=tk.LEFT, padx=5)