fn main() {
    println!("cargo:rerun-if-changed=icon.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let mut res = winresource::WindowsResource::new();
        // Resource ID 1: the exe icon, also loaded by the tray at runtime.
        res.set_icon("icon.ico")
            .set("ProductName", "Steamcorder")
            .set("FileDescription", "Steamcorder")
            .set("CompanyName", "imfeys")
            .set(
                "LegalCopyright",
                "Copyright (c) 2025-2026 imfeys. All rights reserved.",
            );
        if let Err(e) = res.compile() {
            panic!("couldn't embed the Windows resources: {e}");
        }
    }
}
