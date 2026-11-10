import SwiftUI

struct SettingsView: View {
    @Environment(Store.self) private var store

    var body: some View {
        @Bindable var store = store
        Form {
            Section("Display") {
                Toggle(
                    Mutation.pick("M09", "Display quantities", else: "Show quantities"),
                    isOn: $store.showQuantities
                )
                .accessibilityIdentifier(
                    Mutation.pick("M09", "settings.quantities", else: "settings.showQuantities"))
                Toggle("Sort alphabetically", isOn: $store.sortAlphabetically)
                    .accessibilityIdentifier("settings.sortAlphabetically")
            }
            Section {
                Button(Mutation.pick("M06", "Sign Out", else: "Log Out"), role: .destructive) {
                    store.logOut()
                }
                .accessibilityIdentifier("settings.logout")
            }
            Section("About") {
                HStack {
                    Text("Mutation")
                    Spacer()
                    Text(Mutation.current ?? "none").accessibilityIdentifier("settings.mutation")
                }
            }
        }
        .navigationTitle("Settings")
    }
}
