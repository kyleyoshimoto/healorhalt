import SwiftUI

struct SettingsView: View {
    @Environment(Store.self) private var store

    var body: some View {
        @Bindable var store = store
        Form {
            if Mutation.isActive("M12") {
                // M12 (B1+B2+B3): Log Out moves into a new Account section at the top, reworded
                // and with a new identifier.
                Section("Account") {
                    Button("Sign out of Pantry", role: .destructive) {
                        store.logOut()
                    }
                    .accessibilityIdentifier("settings.account.signOut")
                }
            }
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
            if !Mutation.isActive("M12") {
                Section {
                    Button(Mutation.pick("M06", "Sign Out", else: "Log Out"), role: .destructive) {
                        store.logOut()
                    }
                    .accessibilityIdentifier("settings.logout")
                }
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
