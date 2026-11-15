// No console window next to the app window on Windows (release builds).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    jacaranda_parse_lib::run()
}
