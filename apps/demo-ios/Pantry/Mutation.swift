import Foundation

/// The benchmark mutation this launch should apply, read from `-mutation <ID>` on the command line.
///
/// Launch arguments of the form `-key value` land in UserDefaults' argument domain, so each
/// benchmark case needs only a relaunch, not a rebuild. Views check `Mutation.isActive("M07")`
/// to change their UI. The catalog, with each case's expected verdict, is
/// bench/mutations/demo.yaml.
enum Mutation {
    static let current: String? = UserDefaults.standard.string(forKey: "mutation")

    static func isActive(_ id: String) -> Bool {
        current == id
    }

    /// `mutated` when mutation `id` is active, otherwise `original`.
    static func pick<T>(_ id: String, _ mutated: T, else original: T) -> T {
        isActive(id) ? mutated : original
    }
}
