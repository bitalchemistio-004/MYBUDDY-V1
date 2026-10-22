//! Which of the backend's `fired_reminders` the shell still has to show.
//!
//! The backend decides what is due and keeps the newest few in its status
//! snapshot; the shell only displays each one once. A recurring reminder
//! reuses its id, so an entry is identified by id and firing time together.

use serde::Deserialize;
use std::collections::HashSet;

#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct FiredReminder {
    pub id: String,
    pub title: String,
    #[serde(default)]
    pub source: String,
    pub fired_at: f64,
}

impl FiredReminder {
    fn key(&self) -> String {
        format!("{}@{}", self.id, self.fired_at)
    }
}

pub struct ReminderTracker {
    /// Entries fired before this moment (seconds since the epoch) belong to
    /// an earlier run of the shell and are not shown again.
    since: f64,
    shown: HashSet<String>,
}

impl ReminderTracker {
    pub fn new(since: f64) -> Self {
        Self { since, shown: HashSet::new() }
    }

    /// The entries in `current` not shown yet, oldest first.
    pub fn take_new(&mut self, current: &[FiredReminder]) -> Vec<FiredReminder> {
        let fresh: Vec<FiredReminder> = current
            .iter()
            .filter(|r| r.fired_at >= self.since && !self.shown.contains(&r.key()))
            .cloned()
            .collect();
        for r in &fresh {
            self.shown.insert(r.key());
        }
        // The backend keeps only its newest entries, so one that left the
        // list can never come back; forgetting it keeps this set bounded.
        let present: HashSet<String> = current.iter().map(FiredReminder::key).collect();
        self.shown.retain(|k| present.contains(k));
        fresh
    }
}

/// Reads `fired_reminders` out of a `/status` snapshot. Missing or malformed
/// entries are skipped rather than failing the whole poll.
pub fn parse_fired(status: &serde_json::Value) -> Vec<FiredReminder> {
    status
        .get("fired_reminders")
        .and_then(|v| v.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| serde_json::from_value(item.clone()).ok())
                .collect()
        })
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn r(id: &str, at: f64) -> FiredReminder {
        FiredReminder { id: id.into(), title: id.into(), source: "task".into(), fired_at: at }
    }

    #[test]
    fn shows_each_entry_once() {
        let mut t = ReminderTracker::new(100.0);
        let list = vec![r("task:1", 101.0)];
        assert_eq!(t.take_new(&list).len(), 1);
        assert!(t.take_new(&list).is_empty());
    }

    #[test]
    fn skips_entries_from_before_the_shell_started() {
        let mut t = ReminderTracker::new(100.0);
        assert!(t.take_new(&[r("task:1", 50.0)]).is_empty());
    }

    #[test]
    fn recurring_reminder_with_same_id_shows_again() {
        let mut t = ReminderTracker::new(100.0);
        t.take_new(&[r("reminder:3", 101.0)]);
        let again = t.take_new(&[r("reminder:3", 101.0), r("reminder:3", 200.0)]);
        assert_eq!(again, vec![r("reminder:3", 200.0)]);
    }

    #[test]
    fn forgets_entries_that_left_the_list() {
        let mut t = ReminderTracker::new(0.0);
        t.take_new(&[r("a", 1.0)]);
        t.take_new(&[r("b", 2.0)]);
        assert_eq!(t.shown.len(), 1);
    }

    #[test]
    fn parses_status_and_skips_malformed_entries() {
        let status = json!({"fired_reminders": [
            {"id": "task:1", "title": "stretch", "source": "task", "due": "x", "fired_at": 5.5},
            {"id": "broken"}
        ]});
        assert_eq!(parse_fired(&status), vec![FiredReminder {
            id: "task:1".into(), title: "stretch".into(), source: "task".into(), fired_at: 5.5
        }]);
        assert!(parse_fired(&json!({})).is_empty());
    }
}
