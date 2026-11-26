//! The window: a home page with status and activity, and a settings page.

use std::path::{Component, Path};
use std::sync::Arc;
use std::time::{Duration, Instant};

use eframe::egui::{
    self, Align, Align2, Color32, CornerRadius, CursorIcon, FontData, FontDefinitions, FontFamily,
    FontId, Frame, Key, Layout, Margin, Pos2, Rect, Response, RichText, ScrollArea, Sense, Stroke,
    StrokeKind, TextEdit, TextFormat, TextureHandle, Ui, ViewportCommand, pos2, text::LayoutJob,
    vec2,
};

use crate::core::{Changed, Core, Level};
use crate::{APP_NAME, APP_VERSION, DISCORD_INVITE, MAX_DELAY_SECONDS, win};

// ----------------------------------------------------------------------
// Look and feel
// ----------------------------------------------------------------------
const BG: Color32 = Color32::from_rgb(0x0e, 0x10, 0x14);
const SURFACE: Color32 = Color32::from_rgb(0x16, 0x19, 0x20);
const RAISED: Color32 = Color32::from_rgb(0x1e, 0x22, 0x2b);
const BORDER: Color32 = Color32::from_rgb(0x2a, 0x2f, 0x3a);
const TEXT: Color32 = Color32::from_rgb(0xe7, 0xe9, 0xee);
const MUTED: Color32 = Color32::from_rgb(0x8a, 0x91, 0x9e);
const ACCENT: Color32 = Color32::from_rgb(0x5b, 0x8c, 0xff);
const ACCENT_HOVER: Color32 = Color32::from_rgb(0x7a, 0xa2, 0xff);
const SUCCESS: Color32 = Color32::from_rgb(0x3f, 0xcf, 0x8e);
const WARNING: Color32 = Color32::from_rgb(0xf2, 0xb8, 0x4b);
const ERROR: Color32 = Color32::from_rgb(0xf0, 0x6b, 0x6b);

// Qt point sizes at 96 DPI, in egui points.
const BODY: f32 = 13.5;
const SMALL: f32 = 12.0;
const SECTION: f32 = 10.7;
const PRIMARY: f32 = 14.7;
const STATUS: f32 = 16.0;
const TITLE: f32 = 20.0;

const ICON_PNG: &[u8] = include_bytes!("../icon.png");
const NOTICES: &str = include_str!("../THIRD_PARTY_NOTICES.md");

const HOW_TO: &str = "**1. Turn on uncompressed screenshots in Steam**  \n\
Steam → Settings → In Game → *Save an uncompressed copy*, and pick a folder.\n\n\
**2. Choose that folder here**  \n\
Click *Browse* next to Screenshot folder.\n\n\
**3. Create a Discord webhook**  \n\
Server Settings → Integrations → Webhooks → *New Webhook* → *Copy Webhook URL*, \
then paste it here and click *Test*.\n\n\
**4. Start watching**  \n\
Go back and click *Start watching*. New screenshots are posted automatically.";

fn semibold() -> FontFamily {
    FontFamily::Name("semibold".into())
}

fn font(size: f32) -> FontId {
    FontId::proportional(size)
}

fn bold(size: f32) -> FontId {
    FontId::new(size, semibold())
}

/// Use Segoe UI like the rest of Windows, with egui's fonts as fallback.
fn setup_fonts(ctx: &egui::Context) {
    let mut fonts = FontDefinitions::default();
    let defaults = fonts
        .families
        .get(&FontFamily::Proportional)
        .cloned()
        .unwrap_or_default();
    let dir = std::env::var_os("WINDIR").map_or_else(
        || Path::new(r"C:\Windows").join("Fonts"),
        |w| Path::new(&w).join("Fonts"),
    );
    let mut load = |name: &str, file: &str| -> bool {
        match std::fs::read(dir.join(file)) {
            Ok(bytes) => {
                fonts
                    .font_data
                    .insert(name.to_owned(), Arc::new(FontData::from_owned(bytes)));
                true
            }
            Err(_) => false,
        }
    };
    let regular = load("segoe", "segoeui.ttf");
    let semi = load("segoe-semibold", "seguisb.ttf");
    let symbols = load("segoe-symbol", "seguisym.ttf");

    let mut proportional = Vec::new();
    let mut semibold_list = Vec::new();
    if semi {
        semibold_list.push("segoe-semibold".to_owned());
    }
    if regular {
        proportional.push("segoe".to_owned());
        semibold_list.push("segoe".to_owned());
    }
    if symbols {
        proportional.push("segoe-symbol".to_owned());
        semibold_list.push("segoe-symbol".to_owned());
    }
    proportional.extend(defaults.iter().cloned());
    semibold_list.extend(defaults);
    fonts
        .families
        .insert(FontFamily::Proportional, proportional);
    fonts.families.insert(semibold(), semibold_list);
    ctx.set_fonts(fonts);
}

fn setup_style(ctx: &egui::Context) {
    ctx.set_theme(egui::Theme::Dark);
    ctx.all_styles_mut(|style| {
        use egui::TextStyle;
        style.text_styles = [
            (TextStyle::Body, font(BODY)),
            (TextStyle::Button, font(BODY)),
            (TextStyle::Small, font(SMALL)),
            (TextStyle::Heading, bold(TITLE)),
            (TextStyle::Monospace, FontId::monospace(SMALL)),
        ]
        .into();
        style.spacing.item_spacing = vec2(8.0, 6.0);
        style.spacing.button_padding = vec2(14.0, 7.0);
        style.spacing.interact_size.y = 32.0;

        let v = &mut style.visuals;
        v.dark_mode = true;
        v.panel_fill = BG;
        v.window_fill = SURFACE;
        v.window_stroke = Stroke::new(1.0, BORDER);
        v.window_corner_radius = CornerRadius::same(12);
        v.extreme_bg_color = RAISED;
        v.faint_bg_color = SURFACE;
        v.override_text_color = Some(TEXT);
        v.weak_text_color = Some(MUTED);
        v.hyperlink_color = ACCENT_HOVER;
        v.selection.bg_fill = ACCENT.gamma_multiply(0.55);
        v.selection.stroke = Stroke::new(1.0, ACCENT);
        v.text_cursor.stroke = Stroke::new(1.5, TEXT);
        v.popup_shadow = egui::Shadow::NONE;
        v.window_shadow = egui::Shadow::NONE;

        let w = &mut v.widgets;
        for state in [
            &mut w.noninteractive,
            &mut w.inactive,
            &mut w.hovered,
            &mut w.active,
            &mut w.open,
        ] {
            state.corner_radius = CornerRadius::same(8);
            state.bg_fill = RAISED;
            state.weak_bg_fill = RAISED;
            state.bg_stroke = Stroke::new(1.0, BORDER);
            state.fg_stroke = Stroke::new(1.0, TEXT);
            state.expansion = 0.0;
        }
        w.noninteractive.bg_fill = SURFACE;
        w.hovered.bg_stroke = Stroke::new(1.0, MUTED);
        w.active.weak_bg_fill = BORDER;
        w.active.bg_fill = BORDER;
        w.active.bg_stroke = Stroke::new(1.0, MUTED);
    });
}

fn load_png(bytes: &[u8]) -> Option<(u32, u32, Vec<u8>)> {
    let img = image::load_from_memory_with_format(bytes, image::ImageFormat::Png)
        .ok()?
        .into_rgba8();
    Some((img.width(), img.height(), img.into_raw()))
}

// ----------------------------------------------------------------------
// Entry point
// ----------------------------------------------------------------------
pub fn run(core: Arc<Core>, start_hidden: bool) -> eframe::Result {
    let mut viewport = egui::ViewportBuilder::default()
        .with_title(APP_NAME)
        .with_app_id(APP_NAME)
        .with_inner_size([460.0, 640.0])
        .with_min_inner_size([400.0, 540.0]);
    if let Some((width, height, rgba)) = load_png(ICON_PNG) {
        viewport = viewport.with_icon(egui::IconData {
            rgba,
            width,
            height,
        });
    }
    let options = eframe::NativeOptions {
        viewport,
        ..Default::default()
    };
    eframe::run_native(
        APP_NAME,
        options,
        Box::new(move |cc| Ok(Box::new(SteamApp::new(cc, core, start_hidden)))),
    )
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Page {
    Home,
    Settings,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Dialog {
    Help,
    Licenses,
}

#[derive(Clone, Copy, PartialEq, Eq, Default)]
enum Tone {
    #[default]
    Plain,
    Ok,
    Bad,
}

#[derive(Clone, Default)]
struct Hint {
    text: String,
    tone: Tone,
}

impl Hint {
    fn new(text: impl Into<String>, tone: Tone) -> Self {
        Self {
            text: text.into(),
            tone,
        }
    }
}

struct SteamApp {
    core: Arc<Core>,
    page: Page,
    dialog: Option<Dialog>,
    logo: Option<TextureHandle>,
    webhook_text: String,
    show_webhook: bool,
    testing: bool,
    folder_hint: Hint,
    webhook_hint: Hint,
    /// Webhook typing is saved after a short pause rather than on every keystroke.
    webhook_save_at: Option<Instant>,
    hide_after_first_frame: bool,
    tray_tip_shown: bool,
    quitting: bool,
}

impl SteamApp {
    fn new(cc: &eframe::CreationContext<'_>, core: Arc<Core>, start_hidden: bool) -> Self {
        let ctx = &cc.egui_ctx;
        setup_fonts(ctx);
        setup_style(ctx);
        core.set_context(ctx.clone());
        let logo = load_png(ICON_PNG).map(|(w, h, rgba)| {
            let image = egui::ColorImage::from_rgba_unmultiplied([w as usize, h as usize], &rgba);
            ctx.load_texture("logo", image, egui::TextureOptions::LINEAR)
        });
        let webhook_text = core.settings().webhook_url;
        let mut app = Self {
            core,
            page: Page::Home,
            dialog: None,
            logo,
            webhook_text,
            show_webhook: false,
            testing: false,
            folder_hint: Hint::default(),
            webhook_hint: Hint::default(),
            webhook_save_at: None,
            hide_after_first_frame: start_hidden,
            tray_tip_shown: false,
            quitting: false,
        };
        app.refresh_hints(false);
        app
    }

    fn refresh_hints(&mut self, strict: bool) {
        let s = self.core.settings();
        self.folder_hint = if !s.watch_directory.is_empty() && !s.folder_ok() {
            Hint::new("This folder no longer exists.", Tone::Bad)
        } else if s.watch_directory.is_empty() && strict {
            Hint::new(
                "Choose the folder Steam saves uncompressed screenshots to.",
                Tone::Bad,
            )
        } else {
            Hint::default()
        };
        self.webhook_hint = if !s.webhook_url.is_empty() && !s.webhook_ok() {
            Hint::new("That doesn't look like a Discord webhook URL.", Tone::Bad)
        } else if s.webhook_url.is_empty() && strict {
            Hint::new("Paste a webhook URL from your Discord server.", Tone::Bad)
        } else {
            Hint::default()
        };
    }

    fn quit(&mut self, ctx: &egui::Context) {
        self.quitting = true;
        if self.webhook_save_at.take().is_some() {
            self.core.save();
        }
        self.core.shutdown();
        win::tray_remove();
        ctx.send_viewport_cmd(ViewportCommand::Close);
    }

    fn change(&self, which: Changed, apply: impl FnOnce(&mut crate::settings::Settings)) {
        apply(&mut self.core.lock().settings);
        self.core.setting_changed(which);
    }
}

impl eframe::App for SteamApp {
    /// Runs every frame, also while the window is hidden in the tray.
    fn logic(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        if std::mem::take(&mut self.hide_after_first_frame) {
            ctx.send_viewport_cmd(ViewportCommand::Visible(false));
        }

        let (show, settings_page, quit, test_result) = {
            let mut st = self.core.lock();
            (
                std::mem::take(&mut st.request_show),
                std::mem::take(&mut st.request_settings_page),
                std::mem::take(&mut st.request_quit),
                st.test_result.take(),
            )
        };
        if settings_page {
            self.page = Page::Settings;
            self.refresh_hints(true);
        }
        if show {
            ctx.send_viewport_cmd(ViewportCommand::Visible(true));
            ctx.send_viewport_cmd(ViewportCommand::Minimized(false));
            ctx.send_viewport_cmd(ViewportCommand::Focus);
        }
        if let Some((ok, message)) = test_result {
            self.testing = false;
            self.webhook_hint = if ok {
                Hint::new(format!("✓ {message}"), Tone::Ok)
            } else {
                Hint::new(message, Tone::Bad)
            };
        }
        if quit {
            self.quit(ctx);
            return;
        }

        if ctx.input(|i| i.viewport().close_requested()) && !self.quitting {
            if self.core.lock().settings.minimize_on_exit {
                ctx.send_viewport_cmd(ViewportCommand::CancelClose);
                ctx.send_viewport_cmd(ViewportCommand::Visible(false));
                if !self.tray_tip_shown {
                    win::tray_balloon(
                        APP_NAME,
                        "Still running in the tray. Right-click the icon to quit.",
                    );
                    self.tray_tip_shown = true;
                }
            } else {
                self.quit(ctx);
            }
        }

        if let Some(at) = self.webhook_save_at {
            let now = Instant::now();
            if now >= at {
                self.webhook_save_at = None;
                self.core.setting_changed(Changed::WebhookUrl);
            } else {
                ctx.request_repaint_after(at - now);
            }
        }
    }

    fn ui(&mut self, ui: &mut Ui, frame: &mut eframe::Frame) {
        let page_frame = Frame::new().fill(BG).inner_margin(Margin {
            left: 24,
            right: 24,
            top: 22,
            bottom: 18,
        });
        egui::CentralPanel::default()
            .frame(page_frame)
            .show(ui, |ui| match self.page {
                Page::Home => self.home(ui),
                Page::Settings => self.settings_page(ui, frame),
            });
        self.dialogs(ui.ctx());
    }
}

// ----------------------------------------------------------------------
// Home page
// ----------------------------------------------------------------------
impl SteamApp {
    fn home(&mut self, ui: &mut Ui) {
        let (settings, running, uploads, log) = {
            let st = self.core.lock();
            (
                st.settings.clone(),
                self.core.is_running(),
                st.uploads,
                st.log.clone(),
            )
        };

        // Header
        let mut open_settings = false;
        egui::Sides::new().show(
            ui,
            |ui| {
                if let Some(logo) = &self.logo {
                    ui.add(egui::Image::new((logo.id(), vec2(30.0, 30.0))));
                }
                ui.add_space(6.0);
                ui.vertical(|ui| {
                    ui.spacing_mut().item_spacing.y = 0.0;
                    ui.label(RichText::new(APP_NAME).font(bold(TITLE)));
                    ui.label(
                        RichText::new("Steam screenshots → Discord")
                            .size(SMALL)
                            .color(MUTED),
                    );
                });
            },
            |ui| {
                open_settings = button(ui, "Settings").clicked();
            },
        );
        if open_settings {
            self.page = Page::Settings;
        }
        ui.add_space(10.0);

        // Status card
        card(Margin {
            left: 18,
            right: 18,
            top: 16,
            bottom: 18,
        })
        .show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.spacing_mut().item_spacing.y = 4.0;
            ui.horizontal(|ui| {
                let (rect, _) = ui.allocate_exact_size(vec2(10.0, 10.0), Sense::hover());
                ui.painter().circle_filled(
                    rect.center(),
                    5.0,
                    if running { SUCCESS } else { MUTED },
                );
                ui.add_space(2.0);
                let title = if running {
                    "Watching for screenshots"
                } else {
                    "Paused"
                };
                ui.label(RichText::new(title).font(bold(STATUS)));
            });
            let detail = if running {
                let count = if uploads > 0 {
                    format!("{uploads} uploaded this session")
                } else {
                    "Nothing uploaded yet".to_owned()
                };
                format!("{} · {count}", short_path(&settings.watch_directory))
            } else if settings.folder_ok() && settings.webhook_ok() {
                format!("Ready to watch {}", short_path(&settings.watch_directory))
            } else {
                "Choose a screenshot folder and a Discord webhook in Settings.".to_owned()
            };
            ui.add(egui::Label::new(RichText::new(detail).color(MUTED)).wrap());
            ui.add_space(12.0);
            if primary_button(ui, if running { "Stop" } else { "Start watching" }, running)
                .clicked()
            {
                self.core.toggle_watching();
            }
        });
        ui.add_space(10.0);

        // Activity
        let mut clear = false;
        egui::Sides::new().show(
            ui,
            |ui| section(ui, "ACTIVITY"),
            |ui| clear = link_button(ui, "Clear").clicked(),
        );
        if clear {
            self.core.clear_log();
        }
        card(Margin {
            left: 12,
            right: 8,
            top: 8,
            bottom: 8,
        })
        .show(ui, |ui| {
            ui.set_min_size(ui.available_size());
            if log.is_empty() {
                ui.centered_and_justified(|ui| {
                    ui.add(
                        egui::Label::new(
                            RichText::new("Screenshots you take in Steam will show up here.")
                                .color(MUTED),
                        )
                        .wrap(),
                    );
                });
            } else {
                ScrollArea::vertical()
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        ui.spacing_mut().item_spacing.y = 0.0;
                        for entry in &log {
                            activity_row(ui, entry.level, &entry.message, &entry.time);
                        }
                    });
            }
        });
    }
}

fn activity_row(ui: &mut Ui, level: Level, message: &str, time: &str) {
    let color = match level {
        Level::Info => MUTED,
        Level::Success => SUCCESS,
        Level::Warning => WARNING,
        Level::Error => ERROR,
    };
    let text_color = if level == Level::Error { ERROR } else { TEXT };
    let width = ui.available_width();
    let painter = ui.painter();
    let stamp = painter.layout_no_wrap(time.to_owned(), font(SMALL), MUTED);
    let (pad_x, pad_y, dot, gap) = (4.0, 6.0, 8.0, 10.0);
    let text_width = (width - 2.0 * pad_x - dot - 2.0 * gap - stamp.size().x).max(40.0);
    let text = painter.layout(message.to_owned(), font(BODY), text_color, text_width);
    let line_height = ui.fonts_mut(|f| f.row_height(&font(BODY)));
    let height = text.size().y.max(line_height) + 2.0 * pad_y;

    let (rect, _) = ui.allocate_exact_size(vec2(width, height), Sense::hover());
    let top = rect.top() + pad_y;
    let painter = ui.painter();
    painter.circle_filled(
        pos2(rect.left() + pad_x + dot / 2.0, top + line_height / 2.0),
        dot / 2.0,
        color,
    );
    painter.galley(pos2(rect.left() + pad_x + dot + gap, top), text, text_color);
    let stamp_y = top + (line_height - stamp.size().y) / 2.0;
    painter.galley(
        pos2(rect.right() - pad_x - stamp.size().x, stamp_y),
        stamp,
        MUTED,
    );
}

/// Last few components of a path, e.g. '…/Steam/screenshots'.
pub fn short_path(path: &str) -> String {
    const PARTS: usize = 2;
    let components: Vec<Component<'_>> = Path::new(path).components().collect();
    // Python counts a drive and its root ("C:\") as one part.
    let drive_and_root = matches!(
        components.as_slice(),
        [Component::Prefix(_), Component::RootDir, ..]
    );
    let count = components.len() - usize::from(drive_and_root);
    if count <= PARTS + 1 {
        return path.to_owned();
    }
    let tail: Vec<String> = components[components.len() - PARTS..]
        .iter()
        .map(|c| c.as_os_str().to_string_lossy().into_owned())
        .collect();
    format!("…/{}", tail.join("/"))
}

// ----------------------------------------------------------------------
// Settings page
// ----------------------------------------------------------------------
impl SteamApp {
    fn settings_page(&mut self, ui: &mut Ui, frame: &eframe::Frame) {
        let settings = self.core.settings();

        let mut back = false;
        egui::Sides::new().show(
            ui,
            |ui| {
                ui.vertical(|ui| {
                    ui.spacing_mut().item_spacing.y = 0.0;
                    ui.label(RichText::new("Settings").font(bold(TITLE)));
                    ui.label(
                        RichText::new("Changes are saved automatically")
                            .size(SMALL)
                            .color(MUTED),
                    );
                });
            },
            |ui| back = button(ui, "← Back").clicked(),
        );
        if back {
            self.page = Page::Home;
        }
        ui.add_space(14.0);

        // Folder
        section(ui, "SCREENSHOT FOLDER");
        let mut browse = false;
        row(ui, |ui| {
            browse = button(ui, "Browse").clicked();
            read_only_field(ui, &settings.watch_directory, "No folder selected");
        });
        hint(ui, &self.folder_hint);
        if browse {
            self.browse(frame, &settings.watch_directory);
        }
        ui.add_space(10.0);

        // Webhook
        section(ui, "DISCORD WEBHOOK");
        let (mut test, mut toggle_show, mut edited) = (false, false, false);
        let can_test = settings.webhook_ok() && !self.testing;
        row(ui, |ui| {
            let label = if self.testing { "Testing…" } else { "Test" };
            test = ui
                .add_enabled_ui(can_test, |ui| button(ui, label))
                .inner
                .clicked();
            let show_label = if self.show_webhook { "Hide" } else { "Show" };
            toggle_show = toggle_button(ui, show_label, self.show_webhook).clicked();
            let edit = TextEdit::singleline(&mut self.webhook_text)
                .password(!self.show_webhook)
                .hint_text(RichText::new("https://discord.com/api/webhooks/…").color(MUTED))
                .margin(Margin::symmetric(10, 7))
                .desired_width(f32::INFINITY);
            edited = ui.add(edit).changed();
        });
        hint(ui, &self.webhook_hint);
        if toggle_show {
            self.show_webhook = !self.show_webhook;
        }
        if edited {
            let url = self.webhook_text.trim().to_owned();
            self.core.lock().settings.webhook_url = url;
            self.refresh_hints(false);
            self.webhook_save_at = Some(Instant::now() + Duration::from_millis(400));
        }
        if test {
            self.testing = true;
            self.webhook_hint = Hint::new("Sending a test message…", Tone::Plain);
            self.core.test_webhook(self.core.settings().webhook_url);
        }
        ui.add_space(10.0);

        // Delay
        section(ui, "UPLOAD DELAY");
        let mut delay = settings.upload_delay;
        row(ui, |ui| {
            let (rect, _) = ui.allocate_exact_size(vec2(36.0, 20.0), Sense::hover());
            ui.painter().text(
                rect.right_center(),
                Align2::RIGHT_CENTER,
                format!("{delay} s"),
                font(BODY),
                MUTED,
            );
            if delay_slider(ui, &mut delay) {
                self.change(Changed::UploadDelay, |s| s.upload_delay = delay);
            }
        });
        hint(
            ui,
            &Hint::new(
                "Extra wait after a screenshot is saved, before it's posted.",
                Tone::Plain,
            ),
        );
        ui.add_space(10.0);

        // Options
        section(ui, "OPTIONS");
        let flag = |ui: &mut Ui, text: &str, value: bool, which: Changed| {
            let mut checked = value;
            if checkbox(ui, &mut checked, text).changed() {
                self.change(which, |s| match which {
                    Changed::DeleteAfterUpload => s.delete_after_upload = checked,
                    Changed::MinimizeOnExit => s.minimize_on_exit = checked,
                    Changed::StartOnStartup => s.start_on_startup = checked,
                    _ => {}
                });
            }
        };
        flag(
            ui,
            "Delete screenshots after uploading",
            settings.delete_after_upload,
            Changed::DeleteAfterUpload,
        );
        flag(
            ui,
            "Keep running in the tray when closed",
            settings.minimize_on_exit,
            Changed::MinimizeOnExit,
        );
        flag(
            ui,
            "Start with Windows",
            settings.start_on_startup,
            Changed::StartOnStartup,
        );

        // Footer, pinned to the bottom.
        const FOOTER_HEIGHT: f32 = 26.0;
        ui.add_space((ui.available_height() - FOOTER_HEIGHT).max(0.0));
        {
            egui::Sides::new().show(
                ui,
                |ui| {
                    ui.spacing_mut().item_spacing.x = 0.0;
                    if link_button(ui, "How to set up").clicked() {
                        self.dialog = Some(Dialog::Help);
                    }
                    if link_button(ui, "Get support").clicked() {
                        win::open_url(DISCORD_INVITE);
                    }
                    if link_button(ui, "Licenses").clicked() {
                        self.dialog = Some(Dialog::Licenses);
                    }
                },
                |ui| {
                    ui.label(
                        RichText::new(format!("v{APP_VERSION} · by @imfeys"))
                            .size(SMALL)
                            .color(MUTED),
                    );
                },
            );
        }
    }

    fn browse(&mut self, frame: &eframe::Frame, current: &str) {
        let start = if current.is_empty() {
            crate::settings::home_dir()
        } else {
            current.into()
        };
        let picked = rfd::FileDialog::new()
            .set_title("Choose your Steam screenshot folder")
            .set_directory(start)
            .set_parent(frame)
            .pick_folder();
        if let Some(folder) = picked {
            let folder = folder.display().to_string();
            self.core.lock().settings.watch_directory = folder;
            self.refresh_hints(false);
            self.core.setting_changed(Changed::WatchDirectory);
        }
    }

    fn dialogs(&mut self, ctx: &egui::Context) {
        let Some(dialog) = self.dialog else { return };
        let (title, body) = match dialog {
            Dialog::Help => ("How to set up Steamcorder", HOW_TO.to_owned()),
            Dialog::Licenses => (
                "Licenses",
                format!(
                    "**{APP_NAME} {APP_VERSION}**  \nCopyright © imfeys. All rights reserved.\n\n{NOTICES}"
                ),
            ),
        };
        let frame = Frame::new()
            .fill(SURFACE)
            .stroke(Stroke::new(1.0, BORDER))
            .corner_radius(12)
            .inner_margin(Margin::same(20));
        let response = egui::Modal::new(egui::Id::unique("dialog"))
            .frame(frame)
            .show(ctx, |ui| {
                ui.set_width(380.0_f32.min(ctx.content_rect().width() - 80.0));
                ui.label(RichText::new(title).font(bold(STATUS)));
                ui.add_space(8.0);
                ScrollArea::vertical()
                    .max_height(ctx.content_rect().height() - 200.0)
                    .show(ui, |ui| {
                        markdown(ui, &body);
                    });
                ui.add_space(12.0);
                row(ui, |ui| button(ui, "OK").clicked())
            });
        if response.inner || response.should_close() {
            self.dialog = None;
        }
    }
}

// ----------------------------------------------------------------------
// Widgets
// ----------------------------------------------------------------------
/// A single row, laid out right to left so the last widget fills what's left.
fn row<R>(ui: &mut Ui, add: impl FnOnce(&mut Ui) -> R) -> R {
    let size = vec2(ui.available_width(), 33.0);
    ui.allocate_ui_with_layout(size, Layout::right_to_left(Align::Center), add)
        .inner
}

fn card(margin: Margin) -> Frame {
    Frame::new()
        .fill(SURFACE)
        .stroke(Stroke::new(1.0, BORDER))
        .corner_radius(12)
        .inner_margin(margin)
}

fn section(ui: &mut Ui, text: &str) {
    ui.label(RichText::new(text).font(bold(SECTION)).color(MUTED));
}

fn hint(ui: &mut Ui, hint: &Hint) {
    if hint.text.is_empty() {
        return;
    }
    let color = match hint.tone {
        Tone::Plain => MUTED,
        Tone::Ok => SUCCESS,
        Tone::Bad => ERROR,
    };
    ui.add(egui::Label::new(RichText::new(&hint.text).size(SMALL).color(color)).wrap());
}

fn button(ui: &mut Ui, text: &str) -> Response {
    ui.add(egui::Button::new(text))
        .on_hover_cursor(CursorIcon::PointingHand)
}

fn toggle_button(ui: &mut Ui, text: &str, checked: bool) -> Response {
    let mut b = egui::Button::new(text);
    if checked {
        b = b.stroke(Stroke::new(1.0, ACCENT));
    }
    ui.add(b).on_hover_cursor(CursorIcon::PointingHand)
}

/// Borderless text button that brightens on hover.
fn link_button(ui: &mut Ui, text: &str) -> Response {
    let galley = ui
        .painter()
        .layout_no_wrap(text.to_owned(), font(BODY), MUTED);
    let pad = vec2(6.0, 4.0);
    let (rect, response) = ui.allocate_exact_size(galley.size() + 2.0 * pad, Sense::click());
    let color = if response.hovered() { TEXT } else { MUTED };
    ui.painter().galley(rect.min + pad, galley, color);
    response.widget_info(|| egui::WidgetInfo::labeled(egui::Role::Button, true, text));
    response.on_hover_cursor(CursorIcon::PointingHand)
}

/// The big Start/Stop button.
fn primary_button(ui: &mut Ui, text: &str, running: bool) -> Response {
    let (rect, response) = ui.allocate_exact_size(vec2(ui.available_width(), 42.0), Sense::click());
    let hovered = response.hovered();
    let (fill, stroke, color) = if running {
        let edge = if hovered { ERROR } else { BORDER };
        (
            RAISED,
            Stroke::new(1.0, edge),
            if hovered { ERROR } else { TEXT },
        )
    } else {
        (
            if hovered { ACCENT_HOVER } else { ACCENT },
            Stroke::NONE,
            Color32::WHITE,
        )
    };
    let painter = ui.painter();
    painter.rect(rect, 8, fill, stroke, StrokeKind::Inside);
    painter.text(
        rect.center(),
        Align2::CENTER_CENTER,
        text,
        bold(PRIMARY),
        color,
    );
    if response.has_focus() {
        painter.rect_stroke(
            rect.expand(2.0),
            10,
            Stroke::new(1.0, ACCENT),
            StrokeKind::Outside,
        );
    }
    response.widget_info(|| egui::WidgetInfo::labeled(egui::Role::Button, true, text));
    response.on_hover_cursor(CursorIcon::PointingHand)
}

/// A read-only path box, cut short with "…" when it doesn't fit.
fn read_only_field(ui: &mut Ui, text: &str, placeholder: &str) {
    let (rect, _) = ui.allocate_exact_size(vec2(ui.available_width(), 33.0), Sense::hover());
    let painter = ui.painter();
    painter.rect(
        rect,
        8,
        RAISED,
        Stroke::new(1.0, BORDER),
        StrokeKind::Inside,
    );
    let shown = if text.is_empty() { placeholder } else { text };
    let mut job =
        LayoutJob::single_section(shown.to_owned(), TextFormat::simple(font(BODY), MUTED));
    job.wrap = egui::text::TextWrapping::truncate_at_width(rect.width() - 20.0);
    let galley = ui.fonts_mut(|f| f.layout_job(job));
    painter.galley(
        pos2(rect.left() + 10.0, rect.center().y - galley.size().y / 2.0),
        galley,
        MUTED,
    );
}

fn checkbox(ui: &mut Ui, checked: &mut bool, text: &str) -> Response {
    let galley = ui
        .painter()
        .layout_no_wrap(text.to_owned(), font(BODY), TEXT);
    let size = vec2(
        16.0 + 10.0 + galley.size().x,
        galley.size().y.max(16.0) + 10.0,
    );
    let (rect, mut response) = ui.allocate_exact_size(size, Sense::click());
    if response.clicked() {
        *checked = !*checked;
        response.mark_changed();
    }
    let checkbox = Rect::from_min_size(pos2(rect.left(), rect.center().y - 8.0), vec2(16.0, 16.0));
    let painter = ui.painter();
    if *checked {
        painter.rect(
            checkbox,
            5,
            ACCENT,
            Stroke::new(1.0, ACCENT),
            StrokeKind::Inside,
        );
        let p = |x: f32, y: f32| checkbox.min + vec2(x, y) * 0.5;
        painter.line(
            vec![p(8.0, 17.0), p(14.0, 23.0), p(25.0, 10.0)],
            Stroke::new(2.0, Color32::WHITE),
        );
    } else {
        let edge = if response.hovered() || response.has_focus() {
            ACCENT
        } else {
            MUTED
        };
        painter.rect(
            checkbox,
            5,
            RAISED,
            Stroke::new(1.0, edge),
            StrokeKind::Inside,
        );
    }
    painter.galley(
        pos2(
            checkbox.right() + 10.0,
            rect.center().y - galley.size().y / 2.0,
        ),
        galley,
        TEXT,
    );
    let state = *checked;
    response.widget_info(|| egui::WidgetInfo::selected(egui::Role::CheckBox, true, state, text));
    response.on_hover_cursor(CursorIcon::PointingHand)
}

/// 0..=MAX_DELAY_SECONDS slider; drag, click, or use the arrow keys. Returns true if the value changed.
fn delay_slider(ui: &mut Ui, value: &mut u32) -> bool {
    let (rect, response) =
        ui.allocate_exact_size(vec2(ui.available_width(), 22.0), Sense::click_and_drag());
    let radius = 7.0;
    let (x0, x1) = (rect.left() + radius, rect.right() - radius);
    let max = MAX_DELAY_SECONDS;
    let mut v = (*value).min(max);
    if let Some(pos) = response.interact_pointer_pos() {
        let t = ((pos.x - x0) / (x1 - x0)).clamp(0.0, 1.0);
        v = (t * max as f32).round() as u32;
        response.request_focus();
    }
    if response.has_focus() {
        ui.input(|i| {
            if i.key_pressed(Key::ArrowLeft) || i.key_pressed(Key::ArrowDown) {
                v = v.saturating_sub(1);
            }
            if i.key_pressed(Key::ArrowRight) || i.key_pressed(Key::ArrowUp) {
                v = (v + 1).min(max);
            }
            if i.key_pressed(Key::PageDown) {
                v = v.saturating_sub(5);
            }
            if i.key_pressed(Key::PageUp) {
                v = (v + 5).min(max);
            }
            if i.key_pressed(Key::Home) {
                v = 0;
            }
            if i.key_pressed(Key::End) {
                v = max;
            }
        });
    }

    let painter = ui.painter();
    let cy = rect.center().y;
    let x = x0 + (x1 - x0) * (v as f32 / max as f32);
    let groove = Rect::from_min_max(pos2(x0, cy - 2.0), pos2(x1, cy + 2.0));
    painter.rect_filled(groove, 2, BORDER);
    painter.rect_filled(
        Rect::from_min_max(groove.min, pos2(x, groove.max.y)),
        2,
        ACCENT,
    );
    painter.circle_filled(Pos2::new(x, cy), radius, TEXT);
    if response.has_focus() {
        painter.circle_stroke(Pos2::new(x, cy), radius + 2.0, Stroke::new(1.0, ACCENT));
    }
    response.widget_info(|| egui::WidgetInfo::slider(true, f64::from(v), "Upload delay"));
    response.on_hover_cursor(CursorIcon::PointingHand);

    let changed = v != *value;
    *value = v;
    changed
}

// ----------------------------------------------------------------------
// Minimal Markdown for the two dialogs: paragraphs, "  " line breaks,
// headings, **bold**, *italic*, `code` and pipe tables.
// ----------------------------------------------------------------------
fn markdown(ui: &mut Ui, text: &str) {
    let lines: Vec<&str> = text.lines().collect();
    let mut i = 0;
    let mut paragraph = String::new();
    let flush = |ui: &mut Ui, paragraph: &mut String| {
        if !paragraph.is_empty() {
            inline(ui, paragraph);
            ui.add_space(6.0);
            paragraph.clear();
        }
    };
    while i < lines.len() {
        let line = lines[i];
        let trimmed = line.trim();
        if trimmed.is_empty() {
            flush(ui, &mut paragraph);
        } else if let Some(h) = trimmed
            .strip_prefix("## ")
            .or_else(|| trimmed.strip_prefix("# "))
        {
            flush(ui, &mut paragraph);
            ui.add_space(4.0);
            ui.label(RichText::new(h).font(bold(PRIMARY)));
        } else if trimmed.starts_with('|') {
            flush(ui, &mut paragraph);
            let mut rows = Vec::new();
            while i < lines.len() && lines[i].trim().starts_with('|') {
                let cells: Vec<String> = lines[i]
                    .trim()
                    .trim_matches('|')
                    .split('|')
                    .map(|c| c.trim().to_owned())
                    .collect();
                if !cells
                    .iter()
                    .all(|c| !c.is_empty() && c.chars().all(|ch| matches!(ch, '-' | ':' | ' ')))
                {
                    rows.push(cells);
                }
                i += 1;
            }
            table(ui, &rows);
            ui.add_space(6.0);
            continue;
        } else {
            if !paragraph.is_empty() && !paragraph.ends_with('\n') {
                paragraph.push(' ');
            }
            paragraph.push_str(trimmed);
            if line.ends_with("  ") {
                paragraph.push('\n');
            }
        }
        i += 1;
    }
    flush(ui, &mut paragraph);
}

/// A pipe table shown as a narrow list: the first column in bold, the rest
/// below it, so long rows fit a small dialog. The header row is skipped.
fn table(ui: &mut Ui, rows: &[Vec<String>]) {
    for row in rows.iter().skip(1) {
        let Some((name, rest)) = row.split_first() else {
            continue;
        };
        ui.add(
            egui::Label::new(RichText::new(name.trim_matches('`')).font(bold(SMALL))).truncate(),
        );
        ui.horizontal_wrapped(|ui| {
            ui.spacing_mut().item_spacing.x = 10.0;
            for cell in rest {
                if cell.starts_with("https://") {
                    let text = RichText::new(cell).size(SMALL).color(ACCENT_HOVER);
                    let link = ui.add(egui::Label::new(text).truncate().sense(Sense::click()));
                    if link.on_hover_cursor(CursorIcon::PointingHand).clicked() {
                        win::open_url(cell);
                    }
                } else {
                    ui.label(
                        RichText::new(cell.trim_matches('`'))
                            .size(SMALL)
                            .color(MUTED),
                    );
                }
            }
        });
        ui.add_space(4.0);
    }
}

fn inline(ui: &mut Ui, text: &str) {
    let mut job = LayoutJob::default();
    job.wrap.max_width = ui.available_width();
    let (mut is_bold, mut italic, mut code) = (false, false, false);
    let mut rest = text;
    let push = |job: &mut LayoutJob, s: &str, is_bold: bool, italic: bool, code: bool| {
        if s.is_empty() {
            return;
        }
        let font_id = if code {
            FontId::monospace(SMALL)
        } else if is_bold {
            bold(BODY)
        } else {
            font(BODY)
        };
        job.append(
            s,
            0.0,
            TextFormat {
                font_id,
                color: TEXT,
                italics: italic,
                ..Default::default()
            },
        );
    };
    while !rest.is_empty() {
        let next = ["**", "*", "`"]
            .iter()
            .filter_map(|m| rest.find(m).map(|i| (i, *m)))
            .min_by_key(|(i, m)| (*i, -(m.len() as i32)));
        match next {
            Some((i, marker)) => {
                push(&mut job, &rest[..i], is_bold, italic, code);
                match marker {
                    "**" if !code => is_bold = !is_bold,
                    "*" if !code => italic = !italic,
                    "`" => code = !code,
                    other => push(&mut job, other, is_bold, italic, code),
                }
                rest = &rest[i + marker.len()..];
            }
            None => {
                push(&mut job, rest, is_bold, italic, code);
                rest = "";
            }
        }
    }
    ui.label(job);
}

#[cfg(test)]
mod tests {
    use super::short_path;

    #[test]
    fn short_path_keeps_short_paths() {
        assert_eq!(short_path(r"C:\Screens"), r"C:\Screens");
        assert_eq!(short_path(r"C:\Steam\shots"), r"C:\Steam\shots");
        assert_eq!(short_path(""), "");
    }

    #[test]
    fn short_path_shortens_long_paths() {
        assert_eq!(
            short_path(r"C:\Users\me\Steam\screenshots"),
            "…/Steam/screenshots"
        );
        assert_eq!(short_path(r"D:\a\b\c\"), "…/b/c");
        assert_eq!(short_path(r"\\server\share\a\b\c"), "…/b/c");
        assert_eq!(short_path("a/b/c/d"), "…/c/d");
    }
}
