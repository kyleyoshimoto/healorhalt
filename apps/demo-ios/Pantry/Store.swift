import Foundation
import Observation

struct Item: Identifiable, Hashable {
    let id = UUID()
    var name: String
    var quantity: Int
}

/// All app state, kept in memory so every launch starts from the same seed data.
@Observable
final class Store {
    static let username = "demo"
    static let password = "pantry123"

    var isLoggedIn = false
    var items: [Item] = [
        Item(name: "Rice", quantity: 2),
        Item(name: "Eggs", quantity: 12),
        Item(name: "Flour", quantity: 1),
        Item(name: "Milk", quantity: 1),
        Item(name: "Apples", quantity: 6),
    ]
    var showQuantities = true
    var sortAlphabetically = false

    func logIn(username: String, password: String) -> Bool {
        isLoggedIn = username == Store.username && password == Store.password
        return isLoggedIn
    }

    func logOut() {
        isLoggedIn = false
    }

    func visibleItems(matching search: String) -> [Item] {
        let query = search.trimmingCharacters(in: .whitespaces)
        let matches = query.isEmpty
            ? items
            : items.filter { $0.name.localizedCaseInsensitiveContains(query) }
        return sortAlphabetically
            ? matches.sorted { $0.name.localizedCompare($1.name) == .orderedAscending }
            : matches
    }

    func item(_ id: UUID) -> Item? {
        items.first { $0.id == id }
    }

    func add(name: String, quantity: Int) {
        items.append(Item(name: name, quantity: quantity))
    }

    func update(_ id: UUID, name: String, quantity: Int) {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        items[index].name = name
        items[index].quantity = quantity
    }

    func delete(_ id: UUID) {
        items.removeAll { $0.id == id }
    }
}
