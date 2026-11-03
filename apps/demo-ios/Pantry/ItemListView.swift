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
                TextField("Search items", text: $search)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textFieldStyle(.roundedBorder)
                    .padding(.horizontal)
                    .accessibilityIdentifier("list.search")
                Text(store.items.count == 1 ? "1 item" : "\(store.items.count) items")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .accessibilityIdentifier("list.count")
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
                ToolbarItem(placement: .topBarLeading) {
                    NavigationLink(value: Route.settings) {
                        Image(systemName: "gearshape")
                    }
                    .accessibilityLabel("Settings")
                    .accessibilityIdentifier("list.settings")
                }
                ToolbarItem(placement: .topBarTrailing) {
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
}
