import SwiftUI

struct SettingsView: View {
    @Environment(Store.self) private var store

    var body: some View {
        @Bindable var store = store
        Form {
            Section("Display") {
                Toggle("Show quantities", isOn: $store.showQuantities)
                    .accessibilityIdentifier("settings.showQuantities")
                Toggle("Sort alphabetically", isOn: $store.sortAlphabetically)
                    .accessibilityIdentifier("settings.sortAlphabetically")
            }
            Section {
                Button("Log Out", role: .destructive) {
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
