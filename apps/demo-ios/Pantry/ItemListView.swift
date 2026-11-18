import SwiftUI

enum Route: Hashable {
    case item(UUID)
    case settings
}

struct ItemListView: View {
    @Environment(Store.self) private var store
    @State private var search = ""
    @State private var isAdding = false

    var body: some View {
        NavigationStack {
            let items = store.visibleItems(matching: search)
            VStack(spacing: 8) {
                // M07 (B3): the search field moves below the item count. M20 moves it too and
                // breaks it (see searchField).
                let moved = Mutation.isActive("M07") || Mutation.isActive("M20")
                if !moved { searchField }
                Text(store.items.count == 1 ? "1 item" : "\(store.items.count) items")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .accessibilityIdentifier("list.count")
                if moved { searchField }
                if items.isEmpty {
                    Spacer()
                    Text("No matching items")
                        .foregroundStyle(.secondary)
                        .accessibilityIdentifier("list.empty")
                    Spacer()
                } else {
                    List(items) { item in
                        NavigationLink(value: Route.item(item.id)) {
                            HStack {
                                Text(item.name)
                                Spacer()
                                if store.showQuantities {
                                    Text("\(item.quantity)")
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                        .accessibilityIdentifier("item.row.\(item.name)")
                    }
                }
            }
            .navigationTitle("Items")
            .toolbar {
                // M14 (B1+B2+B3): the Settings button moves to the trailing side, relabelled
                // "Preferences" and with a new identifier.
                ToolbarItem(placement: Mutation.pick("M14", .topBarTrailing, else: .topBarLeading)) {
                    if Mutation.isActive("M18") {
                        // M18 (R3, bug): the Settings button is renamed (as in M10) and opens
                        // the Add Item form instead of Settings.
                        Button {
                            isAdding = true
                        } label: {
                            Image(systemName: "gearshape")
                        }
                        .accessibilityLabel("Settings")
                        .accessibilityIdentifier("list.preferences")
                    } else {
                    NavigationLink(value: Route.settings) {
                        Image(systemName: "gearshape")
                    }
                    .accessibilityLabel(Mutation.pick("M14", "Preferences", else: "Settings"))
                    .accessibilityIdentifier(
                        Mutation.isActive("M14")
                            ? "nav.preferences"
                            : Mutation.pick("M10", "list.preferences", else: "list.settings"))
                    }
                }
                // M03 (B3): the Add button moves to the leading side, next to Settings.
                ToolbarItem(placement: Mutation.pick("M03", .topBarLeading, else: .topBarTrailing)) {
                    Button {
                        isAdding = true
                    } label: {
                        Image(systemName: "plus")
                    }
                    .accessibilityLabel("Add item")
                    .accessibilityIdentifier("list.add")
                }
            }
            .navigationDestination(for: Route.self) { route in
                switch route {
                case .item(let id):
                    ItemDetailView(id: id)
                case .settings:
                    SettingsView()
                }
            }
            .sheet(isPresented: $isAdding) {
                AddItemView()
            }
        }
    }

    private var searchField: some View {
        // M20 (R5, bug): the search field silently ignores what is typed.
        TextField("Search items", text: Mutation.isActive("M20") ? .constant("") : $search)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .textFieldStyle(.roundedBorder)
            .padding(.horizontal)
            .accessibilityIdentifier("list.search")
    }
}
